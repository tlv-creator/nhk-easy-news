# tools/ · 站点清理与镜像维护的可执行工具

> 这些脚本放在**共享仓库**里，是因为「关键约束必须放在别人也会去的地方」，
> 只写在某个 agent 自己的脚本里 = 没有保护。

## 1. `check_sites_alive.py` —— 受保护站点存活体检

```bash
python3 tools/check_sites_alive.py            # 查 protected-sites.json 里每个域还活着吗
python3 tools/check_sites_alive.py --json     # 机器可读
```

- 清单里**只写事实**（域名 / 项目 / 为什么不能删 / 写在哪个文件里）
- **「现在还活着吗」一律现查** ⇒ 不把会过期的 `status` 写进清单（过期状态会骗人）
- `status == "DELETED"` 的条目是「已误删但要记住」→ 期望它死，不算异常
- 有活着的域死了 → **exit 1**（清理前必须先处理）
- 🔴 网络错误（URLError）**不等于**站点死了 → 重试一次，仍失败只报「网络错误」不报死

## 2. `update_listen_mirror.py` —— 更新听力记录站的加速镜像

```bash
python3 tools/update_listen_mirror.py --show
python3 tools/update_listen_mirror.py https://新域名.space.mcode.cn
```

- canonical（学生用，永不变）：`https://tlv-creator.github.io/nhk-easy-news/listening/`
- mirror（国内加速，**重建就换域名**）→ 必须同步改 `listening/version.json` 和 `listening/index.html`
- 🔴 **409 = 别人刚推过** → 重取 sha 再重试（并发编辑的正常结果，不是错误）
