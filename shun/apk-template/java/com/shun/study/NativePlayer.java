package com.shun.study;

import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.content.res.AssetFileDescriptor;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;

import java.io.File;
import java.io.IOException;

/**
 * 原生播放器：用 Android 平台 MediaPlayer 播音频。
 *
 * 本项目的音频**内置在 APK 里**（assets/audio/epNN.m4a），所以播放顺序是：
 *   1) 本地缓存副本（filesDir/audio/shun_epNN.m4a，用户点过「⬇ 下載」的）—— 最稳，后台播放不受影响
 *   2) APK 内置资源（AssetManager fd）—— 离线可听，零流量
 *   3) 远端 CDN 地址 —— 兜底（万一以后改成不含音频的版本）
 *
 * 为什么不直接用 WebView 的 &lt;audio&gt;：
 *   WebView 媒体栈对 http 明文 / 混合内容 / Private Network Access 的处理随版本变化。
 *   原生 MediaPlayer 直接走 App 自己的网络栈 / 本机 fd，行为可预期。
 *
 * 所有 MediaPlayer 调用都切到主线程（它要求有 Looper 的线程）。
 */
public class NativePlayer {

    private final MediaProxy proxy;
    private final AudioCache cache;
    private final AssetManagerSource assets;
    private final Handler main = new Handler(Looper.getMainLooper());

    private MediaPlayer mp;
    private String epId = "";
    private volatile double pos = 0;      // 秒
    private volatile double dur = 0;       // 秒
    private volatile boolean playing = false;   // **实际**在放吗（MediaPlayer 的真实状态）
    private volatile boolean wantPlay = false;   // **用户意图**：想放吗
    private volatile boolean ready = false;
    private volatile boolean completed = false;
    private volatile String error = "";
    private volatile float speed = 1f;
    private volatile StateListener listener;

    /**
     * 🔴🔴 准备超时（毫秒）：**既没 onPrepared 也没 onError** ⇒ 判定失败。
     *
     * 为什么必须有：远端 CDN 可能「接了连接、但不回数据」（半开连接 / 服务端挂起）
     *   ⇒ MediaPlayer 既不报错、也不准备好 ⇒ 前端永远等不到 ready
     *   ⇒ 用户点了没声音、**什么提示都没有** = 静默失败。
     * ⇒ 这跟网页版的「error 事件 + 看门狗」是同一个病（两头都断），
     *   原生侧也必须有一条「兜底会响」的机制。
     */
    private static final long PREPARE_TIMEOUT_MS = 15000L;
    private volatile Runnable prepareWatch;

    /**
     * 🔴 用户意图与实际状态必须**分开**：
     *   · 准备期（网络流 1~5 秒）MediaPlayer.isPlaying() 恒 false
     *     → 如果直接把它当「播放状态」回给前端，前端的乐观状态会被顶回去
     *     → **播放按钮左右横跳**（❚❚ 和 ▶ 宽度不同，看起来就是左右动）
     *   · 「点了没反应」：准备期调 mp.start() 抛 IllegalStateException，
     *     被 catch 吞掉 → playing 永远 false → 用户以为没点上
     *   ⇒ 意图（wantPlay）与实际（playing）分开，onPrepared 时才按意图启动。
     */
    public interface StateListener {
        void onCompleted();
        void onError(String msg);
    }

    public void setStateListener(StateListener l) {
        this.listener = l;
    }

    /** 🔴 装上「准备超时」看门狗（见 PREPARE_TIMEOUT_MS 的说明）。 */
    private void armPrepareWatch(final MediaPlayer p) {
        cancelPrepareWatch();
        final Runnable r = new Runnable() {
            @Override
            public void run() {
                if (mp != p) return;                        /* 已经换源/释放 → 本次超时作废 */
                if (ready) return;                           /* 已经准备好了 → 不管 */
                if (error != null && !error.isEmpty()) return; /* 已经报错了 → 不管 */
                error = "准备超时（" + (PREPARE_TIMEOUT_MS / 1000) + " 秒没有响应）";
                playing = false;
                wantPlay = false;
                ready = false;
                StateListener l = listener;
                if (l != null) {
                    try { l.onError(error); } catch (Throwable ignored) { }
                }
            }
        };
        prepareWatch = r;
        main.postDelayed(r, PREPARE_TIMEOUT_MS);
    }

    private void cancelPrepareWatch() {
        Runnable r = prepareWatch;
        prepareWatch = null;
        if (r != null) main.removeCallbacks(r);
    }

    private final Runnable ticker = new Runnable() {
        @Override
        public void run() {
            MediaPlayer p = mp;
            if (p != null) {
                try {
                    if (p.isPlaying()) {
                        pos = p.getCurrentPosition() / 1000.0;
                        playing = true;
                    } else {
                        playing = false;
                    }
                    int d = p.getDuration();
                    if (d > 0) dur = d / 1000.0;
                } catch (IllegalStateException ignored) {
                }
            }
            main.postDelayed(this, 200);
        }
    };

    public NativePlayer(MediaProxy proxy, AudioCache cache, AssetManagerSource assets) {
        this.proxy = proxy;
        this.cache = cache;
        this.assets = assets;
        main.post(ticker);
    }

    // ---------------------------------------------------------------- 控制

    /** 播某一集（可从指定秒数开始）。url = 远端 CDN 地址（兜底用）。 */
    public void play(final String url, final double startSec, final float speed) {
        wantPlay = true;
        main.post(new Runnable() {
            @Override
            public void run() {
                doPlay(url, "", url, startSec, speed);
            }
        });
    }

    /** 播内置的一集：本地副本 → 内置资源 → 远端。 */
    public void playEpisode(final String epId, final String url, final double startSec) {
        wantPlay = true;
        main.post(new Runnable() {
            @Override
            public void run() {
                doPlay(url, epId, url, startSec, speed);
            }
        });
    }

    private void doPlay(String url, String epId, String fallbackUrl, double startSec, float speed) {
        release();
        wantPlay = true;              // 🔴 release() 会清掉意图，这里必须重新置回
        this.epId = epId == null ? "" : epId;
        this.speed = speed <= 0 ? 1f : speed;
        this.pos = Math.max(0, startSec);
        this.error = "";
        this.completed = false;
        this.ready = false;
        this.playing = false;

        if (url == null || url.trim().isEmpty()) {
            error = "沒有音頻地址";
            return;
        }

        try {
            MediaPlayer p = new MediaPlayer();
            mp = p;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                p.setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build());
            }
            p.setOnPreparedListener(new MediaPlayer.OnPreparedListener() {
                @Override
                public void onPrepared(MediaPlayer mp) {
                    cancelPrepareWatch();
                    ready = true;
                    error = "";
                    if (pos > 0.2) {
                        try {
                            mp.seekTo((int) (pos * 1000));
                        } catch (Exception ignored) {
                        }
                    }
                    applySpeed(mp, speed);
                    // 🔴 只在**用户确实想放**的时候才启动 —— 准备期间用户按了暂停，
                    //    不能再自己响起来（这跟 onPrepared 之前抛 IllegalStateException 是两码事）
                    if (!wantPlay) {
                        playing = false;
                        return;
                    }
                    try {
                        mp.start();
                        playing = true;
                    } catch (IllegalStateException e) {
                        error = "start 失败: " + e.getMessage();
                    }
                }
            });
            p.setOnErrorListener(new MediaPlayer.OnErrorListener() {
                @Override
                public boolean onError(MediaPlayer mp, int what, int extra) {
                    cancelPrepareWatch();
                    error = "MediaPlayer 错误 what=" + what + " extra=" + extra;
                    playing = false;
                    ready = false;
                    wantPlay = false;
                    StateListener l = listener;
                    if (l != null) {
                        try { l.onError(error); } catch (Throwable ignored) { }
                    }
                    return true;
                }
            });
            p.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
                @Override
                public void onCompletion(MediaPlayer mp) {
                    cancelPrepareWatch();
                    playing = false;
                    wantPlay = false;
                    completed = true;
                    StateListener l = listener;
                    if (l != null) {
                        try { l.onCompleted(); } catch (Throwable ignored) { }
                    }
                }
            });

            // ① 本地缓存副本
            File local = (cache == null) ? null : cache.localFor(url, epId);
            if (local != null) {
                p.setDataSource(local.getAbsolutePath());
            } else {
                // ② APK 内置资源（未压缩 → 能拿 fd，seek 也正常）
                AssetFileDescriptor afd = null;
                if (assets != null && epId != null && !epId.isEmpty()) {
                    afd = assets.openFd("audio/" + epId + ".m4a");
                }
                if (afd != null) {
                    p.setDataSource(afd.getFileDescriptor(), afd.getStartOffset(), afd.getLength());
                } else {
                    // ③ 远端
                    p.setDataSource(fallbackUrl);
                }
            }
            p.prepareAsync();
            /* 🔴 装上准备超时看门狗：半开连接 / 服务端挂起时 MediaPlayer 既不报错也不准备好 */
            armPrepareWatch(p);
        } catch (Throwable e) {
            error = "播放准备失败: " + MediaProxy.describe(e);
            playing = false;
        }
    }

    public void pause() {
        wantPlay = false;                 // 🔴 用户意图：不要了
        main.post(new Runnable() {
            @Override
            public void run() {
                MediaPlayer p = mp;
                if (p == null) return;
                try {
                    if (p.isPlaying()) {
                        pos = p.getCurrentPosition() / 1000.0;
                        p.pause();
                    }
                } catch (IllegalStateException ignored) {
                }
                playing = false;
            }
        });
    }

    public void resume() {
        wantPlay = true;                  // 🔴 用户意图：要放
        main.post(new Runnable() {
            @Override
            public void run() {
                MediaPlayer p = mp;
                if (p == null) {
                    // 🔴 之前这里直接 return → 什么也不做也不报错 → 用户以为「点了没反应」
                    error = "还没有加载音频，请先选一集";
                    return;
                }
                if (!ready) {
                    // 还在准备中 → 别硬调 start（会抛 IllegalStateException 被吞掉），
                    // 交给 onPrepared：它会看 wantPlay 再决定要不要 start。
                    playing = true;      // 意图已记录，前端按钮保持「播放中」
                    return;
                }
                try {
                    if (!p.isPlaying()) {
                        p.start();
                    }
                    playing = true;
                    error = "";
                } catch (IllegalStateException e) {
                    error = "resume 失败: " + e.getMessage();
                }
            }
        });
    }

    public void seek(final double sec) {
        final int ms = (int) (Math.max(0, sec) * 1000);
        pos = Math.max(0, sec);
        main.post(new Runnable() {
            @Override
            public void run() {
                MediaPlayer p = mp;
                if (p == null) return;
                try {
                    p.seekTo(ms);
                } catch (Exception ignored) {
                }
            }
        });
    }

    public void setSpeed(final float s) {
        speed = s <= 0 ? 1f : s;
        main.post(new Runnable() {
            @Override
            public void run() {
                applySpeed(mp, speed);
            }
        });
    }

    private void applySpeed(MediaPlayer p, float s) {
        if (p == null) return;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                android.media.PlaybackParams pp = new android.media.PlaybackParams();
                pp.setSpeed(s);
                p.setPlaybackParams(pp);
            }
        } catch (Exception ignored) {
        }
    }

    public void stop() {
        wantPlay = false;
        main.post(new Runnable() {
            @Override
            public void run() {
                release();
            }
        });
    }

    private void release() {
        cancelPrepareWatch();
        MediaPlayer p = mp;
        mp = null;
        ready = false;
        playing = false;
        wantPlay = false;
        if (p != null) {
            try {
                p.reset();
            } catch (Exception ignored) {
            }
            try {
                p.release();
            } catch (Exception ignored) {
            }
        }
    }

    // ---------------------------------------------------------------- 状态

    public double getPos() {
        return pos;
    }

    public double getDur() {
        return dur;
    }

    /**
     * 「在放吗」—— 按**用户意图**回答（准备期也返回 true）。
     * 🔴 前端拿它渲染播放按钮；如果返回实际状态，准备期会恒 false → 按钮左右横跳。
     */
    public boolean isPlaying() {
        return wantPlay || playing;
    }

    /** 真的在出声吗（MediaPlayer 的真实状态）—— 给「有没有出声」的看门狗用。 */
    public boolean isActuallyPlaying() {
        return playing;
    }

    /** 用户想放吗（意图）。 */
    public boolean isWanted() {
        return wantPlay;
    }

    public boolean isReady() {
        return ready;
    }

    public boolean isCompleted() {
        return completed;
    }

    public String getError() {
        return error;
    }

    public String getEpId() {
        return epId;
    }

    public float getSpeed() {
        return speed;
    }
}
