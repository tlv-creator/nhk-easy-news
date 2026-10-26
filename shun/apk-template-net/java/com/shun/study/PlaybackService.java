package com.shun.study;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.media.session.MediaSession;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

/**
 * 前台播放服务 —— 让「锁屏 / 切后台 / 划掉界面」都能继续出声。
 *
 * 🔴🔴 网络版与旧版的区别：
 *   旧版：服务控制**原生 MediaPlayer**（NativePlayer）
 *   本版：服务控制**页面里的 &lt;audio&gt;**（经 ShunRuntime → WebView）
 *   ⇒ 服务的职责从「播放」变成「**保活 + 锁屏控制**」，
 *     实际出声的是 Chromium 的媒体栈（它不依赖 Activity 是否在前台）。
 *
 * 🔴🔴 三条铁律（每一条都是旧版踩过坑换来的）：
 *
 * 1) **服务移出启动路径**：只在「真的要播了」的时候 ensureStarted()。
 *    放在 MainActivity.onCreate 里 → 主线程那一刻在干重活（建 WebView + loadUrl，
 *    老墨水屏机首屏 >5 秒完全可能）→ 服务 onCreate 拖到 5 秒后 →
 *    系统抛 ForegroundServiceDidNotStartInTimeException → **杀进程 = 闪退**。
 *
 * 2) **startForeground 必须是 onCreate 的第一行**，通知要最简、
 *    不依赖任何其它对象。锦上添花的东西（MediaSession 等）各自 try/catch，
 *    失败只降级，绝不能拖慢这一行。
 *
 * 3) **API 26 的符号全部隔离进 Api26 内部类**：
 *    NotificationChannel / Notification.Builder(Context,String) 在 Android 7 上不存在，
 *    只要**本类的字节码里出现**对它们的引用，ART 校验本类时就会去解析 →
 *    NoClassDefFoundError → 打开 App 秒退。
 *    放进独立类以后，ART 在 API 24/25 上**根本不会加载**那个类 → 100% 安全。
 */
public class PlaybackService extends Service {

    private static final int NOTIF_ID = 0x5A17;

    private MediaSession session;
    private final Handler main = new Handler(Looper.getMainLooper());
    private boolean foreground = false;
    private boolean tickerRunning = false;

    /** 只在真的要播的时候起服务（见类注释第 1 条）。 */
    public static void ensureStarted(Context ctx) {
        try {
            Context app = ctx.getApplicationContext();
            Intent i = new Intent(app, PlaybackService.class);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                app.startForegroundService(i);
            } else {
                app.startService(i);
            }
        } catch (Throwable ignored) { }
    }

    // ---------------------------------------------------------------- 生命周期

    @Override
    public void onCreate() {
        super.onCreate();
        // 🔴🔴 必须是第一行 —— 前台服务 5 秒红线
        try {
            startForeground(NOTIF_ID, buildNotification("日語精読 Shun", "正在播放…"));
            foreground = true;
        } catch (Throwable t) {
            try {
                startForeground(NOTIF_ID, new Notification.Builder(this)
                        .setSmallIcon(android.R.drawable.ic_media_play)
                        .setContentTitle("日語精読 Shun")
                        .setOngoing(true)
                        .build());
                foreground = true;
            } catch (Throwable ignored) { }
        }

        // 锦上添花：MediaSession（失败只降级，不能影响上面那一行）
        try {
            session = new MediaSession(this, "shun");
            session.setCallback(new MediaSession.Callback() {
                @Override
                public void onPlay() {
                    exec("try{if(window.__shunAu){window.__shunAu.play();}}catch(e){}");
                }

                @Override
                public void onPause() {
                    exec("try{if(window.__shunAu){window.__shunAu.pause();}}catch(e){}");
                }

                @Override
                public void onStop() {
                    exec("try{if(window.__shunAu){window.__shunAu.pause();}}catch(e){}");
                }

                @Override
                public void onSeekTo(long pos) {
                    final double sec = pos / 1000.0;
                    exec("try{if(window.__shunAu){window.__shunAu.currentTime=" + sec + ";}}catch(e){}");
                }
            });
            session.setFlags(MediaSession.FLAG_HANDLES_MEDIA_BUTTONS);
            session.setActive(true);
        } catch (Throwable ignored) { }

        startTicker();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        try {
            if (!foreground) {
                startForeground(NOTIF_ID, buildNotification("日誌精読 Shun", "正在播放…"));
                foreground = true;
            }
        } catch (Throwable ignored) { }
        return START_STICKY;      // 被系统回收后自动拉起 → 继续播
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        // 用户把任务划掉了：**不要停服务**（那正是「划掉界面不断声」要的）
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public void onDestroy() {
        try { stopForegroundCompat(); } catch (Throwable ignored) { }
        try {
            if (session != null) { session.setActive(false); session.release(); session = null; }
        } catch (Throwable ignored) { }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // ---------------------------------------------------------------- 播放状态同步

    /**
     * 每秒读一次页面的播放状态，喂给 MediaSession（锁屏进度条 / 播放暂停按钮）。
     * 播放结束 ⇒ 撤通知 + 停服务（否则通知永远挂着）。
     */
    private void startTicker() {
        if (tickerRunning) return;
        tickerRunning = true;
        main.postDelayed(new Runnable() {
            @Override
            public void run() {
                poll();
                main.postDelayed(this, 1000);
            }
        }, 1000);
    }

    private void poll() {
        if (!ShunRuntime.ready()) return;
        ShunRuntime.eval(PlaybackService.STATE_JS, new ShunRuntime.ValueCallback() {
            @Override
            public void onValue(String value) {
                apply(value);
            }
        });
    }

    private void apply(String json) {
        if (json == null || session == null) return;
        try {
            String s = json.trim();
            if (s.startsWith("\"") && s.endsWith("\"")) s = s.substring(1, s.length() - 1);
            s = s.replace("\\\"", "\"").replace("\\\\", "\\");
            org.json.JSONObject o = new org.json.JSONObject(s);
            boolean playing = o.optBoolean("playing", false);
            boolean ended = o.optBoolean("ended", false);
            long pos = o.optLong("pos", 0);
            long dur = o.optLong("dur", 0);
            try {
                android.media.session.PlaybackState st =
                    new android.media.session.PlaybackState.Builder()
                        .setActions(android.media.session.PlaybackState.ACTION_PLAY
                            | android.media.session.PlaybackState.ACTION_PAUSE
                            | android.media.session.PlaybackState.ACTION_PLAY_PAUSE
                            | android.media.session.PlaybackState.ACTION_SEEK_TO
                            | android.media.session.PlaybackState.ACTION_STOP)
                        .setState(playing
                            ? android.media.session.PlaybackState.STATE_PLAYING
                            : (ended ? android.media.session.PlaybackState.STATE_STOPPED
                                     : android.media.session.PlaybackState.STATE_PAUSED),
                            pos, dur > 0 ? 0.9f : 0f)
                        .build();
                session.setPlaybackState(st);
                session.setMetadata(new android.media.MediaMetadata.Builder()
                    .putString(android.media.MediaMetadata.METADATA_KEY_TITLE, "日語精読 Shun")
                    .build());
            } catch (Throwable ignored) { }
            if (ended) {
                stopForegroundCompat();
                stopSelf();
            }
        } catch (Throwable ignored) { }
    }

    private void exec(final String js) {
        ShunRuntime.eval(js, null);
    }

    // ---------------------------------------------------------------- 通知

    private Notification buildNotification(String title, String text) {
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                return Api26.make(this, title, text);
            }
            return new Notification.Builder(this)
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(title)
                .setContentText(text)
                .setOngoing(true)
                .build();
        } catch (Throwable t) {
            return new Notification.Builder(this)
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(title)
                .setOngoing(true)
                .build();
        }
    }

    private void stopForegroundCompat() {
        try {
            if (Build.VERSION.SDK_INT >= 24) {
                stopForeground(Service.STOP_FOREGROUND_REMOVE);
            } else {
                stopForeground(true);
            }
        } catch (Throwable ignored) { }
    }

    /**
     * 🔴 API 26 的符号必须隔离在这里：Android 7（API 24/25）上 ART **根本不会加载**本类
     * ⇒ 绝不会 NoClassDefFoundError（这条在旧版踩过，症状是「打开 App 秒退」）。
     */
    static final class Api26 {
        private Api26() { }

        static Notification make(Context ctx, String title, String text) {
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) {
                try {
                    NotificationChannel ch = new NotificationChannel(
                        "shun_playback", "播放控制", NotificationManager.IMPORTANCE_LOW);
                    ch.setShowBadge(false);
                    ch.setSound(null, null);
                    nm.createNotificationChannel(ch);
                } catch (Throwable ignored) { }
            }
            Notification.Builder b = new Notification.Builder(ctx, "shun_playback");
            b.setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(title)
                .setContentText(text)
                .setOngoing(true);
            return b.build();
        }
    }

    /** 与 MainActivity 保持同一份状态读取脚本（pos/dur 都是毫秒）。 */
    static final String STATE_JS =
        "(function(){try{"
        + "var a=window.__shunAu;"
        + "if(!a) return JSON.stringify({has:false,playing:false,pos:0,dur:0,ended:false});"
        + "var d=(a.duration&&isFinite(a.duration))?a.duration:0;"
        + "return JSON.stringify({has:true,playing:(!a.paused&&!a.ended),pos:Math.round(a.currentTime*1000),"
        + "dur:Math.round(d*1000),ended:!!a.ended});"
        + "}catch(e){return JSON.stringify({has:false,playing:false,pos:0,dur:0,ended:false});}})();";
}
