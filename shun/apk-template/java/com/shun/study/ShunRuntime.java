package com.shun.study;

import android.content.Context;

/**
 * 全局运行时持有者 —— 播放器与本机代理**跨 Activity 复用**。
 *
 * 🔴🔴 为什么必须静态：
 *   Activity 重建（旋转 / 内存回收 / 用户切回）时如果 new 一个新的 NativePlayer，
 *   就会出现「**两个播放器同时出声**」；
 *   代理抢不到固定端口 → 退随机端口 → **origin 变了 → localStorage 全丢**
 *   （用户的收藏 / 观看进度全没了）。
 *   ⇒ 播放器、代理、缓存都必须**静态复用**，谁先初始化谁说了算。
 *
 * 🔴 用 getApplicationContext()，**绝不持有 Activity**（否则泄漏）。
 */
final class ShunRuntime {

    /** 本机代理固定端口：origin 稳定 → localStorage 能持久化 */
    static final int FIXED_PORT = 18789;

    private static MediaProxy proxy;
    private static AssetManagerSource assets;
    private static AudioCache cache;
    private static NativePlayer player;

    private ShunRuntime() { }

    /** 幂等：已初始化就直接复用（绝不重复 new）。 */
    static synchronized void init(Context ctx) {
        if (player != null && proxy != null) return;
        Context app = ctx.getApplicationContext();
        try {
            assets = new AssetManagerSource(app.getAssets());
            cache = new AudioCache(app, assets);
            proxy = new MediaProxy();
            proxy.setAssetSource(assets);
            try {
                proxy.start(FIXED_PORT);
            } catch (Throwable t) {
                // 端口被占（旧进程没退干净）→ 退随机端口。⚠️ 这会让 origin 变、
                // localStorage 丢，所以这里只作为最后手段，且会在设置里能看出来。
                try { proxy.start(); } catch (Throwable ignored) { }
            }
            player = new NativePlayer(proxy, cache, assets);
        } catch (Throwable t) {
            // 初始化失败也不能让 App 起不来 —— 页面还能退到 file:// 兜底
        }
    }

    static MediaProxy proxy() { return proxy; }
    static NativePlayer player() { return player; }
    static AudioCache cache() { return cache; }
}
