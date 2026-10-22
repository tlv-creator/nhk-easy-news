# tools/ · 站点清理与镜像维护的可执行工具

> 这些脚本放在**共享仓库**里，是因为「关键约束必须放在别人也会去的地方」，
> 只写在某个 agent 自己的脚本里 = 没有保护。

## 🔴 先分清「源端」和「渲染端」—— 2026-10-23 加的一条判据

| | 是什么 | 能不能当真相 |
|---|---|---|
| **源端** | 仓库 Contents API（`api.github.com/.../contents/...`） | ✅ **是真相** |
| **渲染端** | GitHub Pages（`tlv-creator.github.io/...`） | ❌ **有构建/缓存延迟，慢一拍是正常的** |

⇒ **「我看到的旧内容」有两种可能**：① 真被覆盖了 ② 还没刷新。
⇒ **复核必须回源**，不能看渲染端；渲染端只用来「报告」，不参与判定。
⇒ 本目录里两个脚本都遵守这条；`check_sites_alive.py` 还会**打印它用的是哪一端**。

## 1. `check_sites_alive.py` —— 受保护站点存活体检

```bash
python3 tools/check_sites_alive.py            # 查 protected-sites.json 里每个域还活着吗
python3 tools/check_sites_alive.py --json     # 机器可读
python3 tools/check_sites_alive.py --local x.json
```

- 清单里**只写事实**（域名 / 项目 / 为什么不能删 / 写在哪个文件里）
- **「现在还活着吗」一律现查** ⇒ 不把会过期的 `status` 写进清单（过期状态会骗人）
  - ⚖️ 但 `status: "DELETED"` **可以写**：它是**永久事实**（删掉的站不会活回来），
    和「现在还活着吗」这种会过期的东西不是一回事 ⇒ **不矛盾**
- 🔴 网络错误（URLError）**不等于**站点死了 → 重试一次，仍失败只报「网络错误」不报死
- **清单从源端读**（Contents API → raw → 才退到 Pages 并明确标注）⇒ 不会拿到还没刷新的旧清单
- 清单拉不到 → **exit 2**（**不许放行删除操作**：闸门找不到东西必须 FAIL）

## 2. `update_listen_mirror.py` —— 更新听力记录站的加速镜像

```bash
python3 tools/update_listen_mirror.py --show
python3 tools/update_listen_mirror.py https://新域名.space.mcode.cn
python3 tools/update_listen_mirror.py https://新域名.space.mcode.cn --no-rendered
```

- canonical（学生用，永不变）：`https://tlv-creator.github.io/nhk-easy-news/listening/`
- mirror（国内加速，**重建就换域名**）→ 必须同步改 `listening/version.json` 和 `listening/index.html`
- 🔴 **409 = 别人刚推过** → **重读、重新合**，不覆盖对方的改动
  （实现上：**用「读时拿到的 sha」去 PUT**。若在 PUT 前现取 sha，409 几乎永不发生 ⇒ 并发改动被静默覆盖）
- 🔴 **选择式替换**：只换「加速通道」那一个链接，页面上别的 mcode 链接（备用通道 B 等）**不误伤**
- 🔴 **复核回源**，且**复核不过 = 退出码非 0**（不是「打印个 ❌ 然后照样退 0」）
- 退出码：`0` = 源端复核通过 ｜ `1` = 失败 ｜ `2` = 环境问题（没 token / 拿不到 sha / 拉不到源端 ⇒ **不写**）

## 3. `test_listen_tools.py` —— 自检（**每写完一个工具，立刻造反例去撞它**）

```bash
python3 tools/test_listen_tools.py
```

自带 mock 仓库（不碰真 GitHub），10 个用例：

| 用例 | 撞什么 |
|---|---|
| 正向 | 新镜像写进去、页面链接跟着换 |
| 反向 A | 读之后别人推过 ⇒ 409 ⇒ **别人的改动不能被覆盖** |
| 反向 B | 写进去的内容不对 ⇒ **退出码必须非 0**（旧版这里是 0 = 假绿灯） |
| 反向 C | 页面上有两个 mcode 链接 ⇒ 只换加速通道那个 |
| 反向 D | 渲染端还是旧内容 ⇒ 退出码仍 0，但**必须说出来** |
| 对拍 1 | **旧版二进制**确实会静默覆盖别人的改动（证明修复不是多余的） |
| 对拍 2 | 旧版二进制：回读是错的却退出 0（假绿灯坐实） |
| 清单 1 | 清单**优先读源端**（渲染端给旧清单时也要用源端那份） |
| 清单 2 | 清单拉不到 ⇒ exit 2（不许放行） |
| 参数 | `--show` 不能走成「打印帮助」 |

🔴 `fixtures/old_update_listen_mirror.py` = **旧版工具的字节级副本**，
只用于上面两条**对拍**用例（仅改了两行端点常量指向 mock，被测逻辑一字未动）。
**它不是第二份可用实现，别直接拿去用。**
