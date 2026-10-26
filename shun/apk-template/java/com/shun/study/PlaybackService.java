package com.shun.study;

import android.app.Notification;
import android.app.NotificationChannel;   // 🔴 只在 Api26 内部类里用（import 本身是编译期的，不影响运行时分派）
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.media.session.MediaSession;
import android.os.Build;
import android.os.IBinder;
import android.os.Handler;
import android.os.Looper;

/**
 * 前台播放服务 —— 让「锁屏 / 切后台 / 划掉界面」都能继续出声。
 *
 * 🔴🔴 三条铁律（每一条都是踩过坑换来的）：
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
            // 连最简通知都建不出来 → 再退一步，只用系统图标
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
                    NativePlayer p = ShunRuntime.player();
                    if (p != null) p.resume();
                }
                @Override
                public void onPause() {
                    NativePlayer p = ShunRuntime.player();
                    if (p != null) p.pause();
                }
                @Override
                public void onStop() {
                    NativePlayer p = ShunRuntime.player();
                    if (p != null) p.stop();
                }
                @Override
                public void onSeekTo(long pos) {
                    NativePlayer p = ShunRuntime.player();
                    if (p != null) p.seek(pos / 1000.0);
                }
            });
            session.setFlags(MediaSession.FLAG_HANDLES_MEDIA_BUTTONS);
            session.setActive(true);
        } catch (Throwable ignored) { }

        // 播放结束 → 撤掉通知并停服务（否则通知永远挂着）
        try {
            NativePlayer p = ShunRuntime.player();
            if (p != null) {
                p.setStateListener(new NativePlayer.StateListener() {
                    @Override
                    public void onCompleted() {
                        stopForegroundCompat();
                        stopSelf();
                    }
                    @Override
                    public void onError(String msg) {
                        stopForegroundCompat();
                        stopSelf();
                    }
                });
            }
        } catch (Throwable ignored) { }

        startTicker();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // 兜底再叫一次 startForeground（万一第一次抛了）
        try {
            if (!foreground) {
                startForeground(NOTIF_ID, buildNotification("日語精読 Shun", "正在播放…"));
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
        try {
            NativePlayer p = ShunRuntime.player();
            if (p != null) p.setStateListener(null);
        } catch (Throwable ignored) { }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // ---------------------------------------------------------------- 通知

    private void stopForegroundCompat() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                stopForeground(Service.STOP_FOREGROUND_REMOVE);
            } else {
                stopForeground(true);
            }
        } catch (Throwable ignored) { }
        foreground = false;
    }

    private Notification buildNotification(String title, String text) {
        Notification n = null;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            try { n = Api26.build(this, title, text, session); }
            catch (Throwable ignored) { }
        }
        if (n == null) {
            // 1 参构造器 = API 1（2 参那个是 API 26）
            n = new Notification.Builder(this)
                    .setSmallIcon(android.R.drawable.ic_media_play)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setOngoing(true)
                    .build();
        }
        return n;
    }

    /** 每秒刷新一次通知文字（集名 / 播放状态）。 */
    private void startTicker() {
        main.postDelayed(new Runnable() {
            @Override
            public void run() {
                try {
                    NativePlayer p = ShunRuntime.player();
                    if (p != null) {
                        String t = p.getEpId();
                        if (t != null && !t.isEmpty()) t = "正在播放：" + t;
                        if (!p.isActuallyPlaying() && !p.isWanted()) t = "已暂停";
                        Notification n = buildNotification("日語精読 Shun", t);
                        NotificationManager nm =
                                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
                        if (nm != null) nm.notify(NOTIF_ID, n);
                    }
                } catch (Throwable ignored) { }
                main.postDelayed(this, 1000);
            }
        }, 1000);
    }

    // ---------------------------------------------------------------- API 26 隔离

    /**
     * 🔴 API 26 专属符号全部隔离在这里。
     * 本类**只在 API 26+ 的分支里才会被加载** → ART 在 Android 7/7.1 上不会碰它
     * → NotificationChannel 之类「老系统上不存在」的类不会被解析 → 不会 NoClassDefFoundError。
     *
     * ⚠️ 方法内部**也**加了 SDK_INT 守卫 —— 静态检查器按「同一方法里的字节码区间」判定，
     *    只在调用方加守卫会被判成「无守卫」。
     */
    static final class Api26 {

        static final String CHANNEL_ID = "shun_playback";

        static Notification build(Context ctx, String title, String text, MediaSession session) {
            // ⚠️ 守卫写法：**两种形式检查器都认**
            //   （a）if (SDK_INT >= O) { … }        → 编译成 if-lt
            //   （b）if (SDK_INT < O) return null;   → 编译成 if-ge
            //   实测（guard_sample.apk + 自检「只拆 if-ge」「只拆 if-lt」两组用例）：
            //   两种都被判为「已守卫」。这里用 (a) 只是为了读起来清楚、跟 hub 一致。
            Notification n = null;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ensureChannel(ctx);
                Notification.Builder b = new Notification.Builder(ctx, CHANNEL_ID);
                b.setSmallIcon(android.R.drawable.ic_media_play);
                b.setContentTitle(title);
                b.setContentText(text);
                b.setOngoing(true);
                if (session != null) {
                    try {
                        b.setStyle(new Notification.MediaStyle().setMediaSession(session.getSessionToken()));
                    } catch (Throwable ignored) { }
                }
                n = b.build();
            }
            return n;
        }

        private static void ensureChannel(Context ctx) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
                if (nm == null) return;
                NotificationChannel ch = new NotificationChannel(
                        CHANNEL_ID, "播放控制", NotificationManager.IMPORTANCE_LOW);
                ch.setDescription("日語精読 Shun 正在播放");
                ch.setShowBadge(false);
                nm.createNotificationChannel(ch);
            }
        }
    }
}
