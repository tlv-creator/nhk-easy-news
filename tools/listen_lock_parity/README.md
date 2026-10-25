# 听力站收尾 SQL · 独立对拍台（第三方复核用）

这是**独立第三方**在本地 PostgreSQL 15 上对 `tools/listen_lock_anon_delete.sql`
（sha256 行令牌版）做的验证台子，**不依赖同伴的回归台**。

## 四个文件各证明什么

| 文件 | 证明什么 | 怎么跑 |
|---|---|---|
| `setup.sql` | 造一个**真实库形状**的台子：老师侧 `FOR ALL TO authenticated` + 一条**混合型** policy + anon 整班删（收紧前）+ LISTEN 两条带 sha256 令牌 + 探针行 + 词汇站四班 2462 行 | `psql -h <socket目录> -d <库名> -f setup.sql` |
| `tests.sql` | **13 条场景断言**（每条用事务+回滚隔离）：词汇站整班删不掉 / LISTEN 整班删不掉 / **偷到的 sha256 摘要当令牌也删不掉** / 本人带原始令牌能删 / 拿别人令牌冒充删不掉 / 探针可清 / LIKE 通配符注入被挡 / 老师侧三种删除都成功 / 探针行被清 / policy 形状 / **哈希以 0 开头仍能删** | `psql -d vt -f tests.sql` |
| `test_setup_guard.sh` | **闸门自检**（不是靠注释，是靠真的会拒绝执行）：① 闸门与 `ON_ERROR_STOP` 必须在文件里 ② unix socket ⇒ 放行且**完整跑完**（表 3/3 + policy ≥3 + 零报错）③ TCP ⇒ **被拒**且三张表和里面的数据**一个都不能少** ④ 角色必须先建（否则 grant 报错 ⇒ 脚本中途死掉 ⇒ policy 没建全） | `bash test_setup_guard.sh`（需本地 postgres；PGROOT 指到 `/var/tmp` 更快） |
| `hash_parity.js` | **跨实现对拍**：浏览器形态（`crypto.subtle`，逐字照抄线上 api.js 的 `sha256Hex`）vs PG 形态（`encode(sha256(convert_to(t,'utf8')),'hex')`）—— 9 个用例（典型×3 / **哈希以 0 开头** / 空串 / 1 字符 / 64 字符 / 非 ASCII / 含大写）逐字相等；外加一个 **BigInt 不补零** 的反面对照 | `node hash_parity.js` |

## ⚠️ 怎么跑（setup.sql 带闸门，**跑法有要求**）

`setup.sql` 第一段有效语句是**闸门**：

```sql
\set ON_ERROR_STOP on
do $$ begin
  if inet_server_addr() is not null then
    raise exception 'setup.sql 只能在本地 unix socket 的库上跑（当前是 TCP 连接）……';
  end if;
end $$;
```

- **必须用 unix socket**：`psql -h <socket目录> -d <你的库名> -f setup.sql`
  （不写 `-h` 时 psql 默认也走 socket；`-h 127.0.0.1` 会被**拒**）
- **本地但走 TCP 也会被拒** —— 这是**故意的 fail-closed**：宁可报错，也不让人误在生产库上跑
- 判据用 `inet_server_addr()`，**不用库名白名单** —— 各人的本地库名不一样（rlstest / vt / …），
  白名单必误伤
- `\set ON_ERROR_STOP on` 是**闸门的一部分**：没有它，psql 报错后会继续往下跑，
  `drop table` 照样执行 ⇒ 闸门等于装饰
- 这个闸门**只在夹具里**：老师要跑的收尾 SQL 是**远程 TCP** 跑的，加 socket 判据会让老师直接跑不了
- ⚠️ **端点判据不能只看 405**：**打错主机也会回 405**（OSS 静态托管的 XML
  `<Code>MethodNotAllowed</Code>`）⇒ 必须是 **405 + 响应里含 `"service":"vocab-ops"` 双条件**；
  地址要认准 `vocab-test-…service.tcloudbase.com` 那个**云函数域名**，不是学生镜像站
- `setup.sql` 会**幂等先建** `anon` / `authenticated` / `service_role` 三个角色
  （**全新本地库里没有这三个角色** ⇒ `grant` 会报错 ⇒ 配合 `ON_ERROR_STOP` ⇒ 脚本会**死在一半**：
  表建好了但 policy 一条没建）⇒ 现在 `initdb → createdb → psql -f setup.sql` 能一次跑完
- 想要**机器可查**的自检（不靠注释，靠真的会拒绝执行）：
  `bash tools/listen_lock_parity/test_setup_guard.sh`（8 条断言：闸门在不在 / socket 放行且跑完 /
  TCP 被拒且数据一个不少 / 角色先建）

## ⚠️ 完整 E2E（两个脚本）跑之前必读

| 文件 | 证明什么 | 怎么跑 |
|---|---|---|
| `tools/listen_e2e_identity.py` | **共用底座**（唯一权威实现）：名册加载（本地 → 线上，线上**重试 3 次**）+ 身份解析 + 落盘目录；**自带 7 条自测** | `python3 tools/listen_e2e_identity.py` |
| `tools/check_listen_browser.py` / `tools/listen_browser_e2e.py` | **浏览器级闭环**：学生登录 → 交一条 → 库里在 → 学生删自己的 → 库里没了 → 教师登录 → 删一条 → 清空全部护栏 ⇒ **24 条断言** | `python3 tools/listen_browser_e2e.py`（🔴 **别加 `--locked`**） |

### 🔴 别加 `--locked`（现在洞还没关）
- `--locked` 的含义是「收尾 SQL 已跑完、anon 整班删已被拒」⇒ **只有第三步做完之后**才加
- 现在加 ⇒ 脚本会拿 anon 去整班删 LISTEN ⇒ 按设计会被拒（**不会删到数据**），
  但它会把「洞没关上」判成失败 ⇒ **误报**，而且是在真实库上试
- 不加 `--locked` 时：③④ 那段真删**整段跳过**（脚本会打印「anon 整班删还开着，这是预期的」）

### 为什么这样跑是安全的（三层，逐层查过才跑）
1. **③④ 的真删在 `if LOCKED:` 里** ⇒ 不加 `--locked` ⇒ 整段跳过
2. **③「教师删一条」删的是探针，不是真记录** ⇒ 现役 `api.js` 用 `order=id.desc`
   ⇒ 明细表**第一行 = 最新 id = 刚 POST 的探针行**
3. **④-2 端点没上线 ⇒ 自动跳过**（回包 `endpoint_unreachable: 404`）⇒ 不拿真班级当试验田

兜底报警：脚本最后一条判据是「**跑前就在的真记录一条没丢、id 没被换**」
⇒ 真记录被动过就必然红（实测 PASS）。

### 🔴 线上名册兜底会**重试 3 次**（不是单次）
部分网络的 DNS 会**间歇整段挂**（`options ndots:6` + 多个 search 域 ⇒
任一个 search 域回 SERVFAIL ⇒ glibc 整次放弃）⇒ **单次失败 ≠ 名册没了**
⇒ 线上兜底重试 3 次（间隔 1.5s），三次全挂才判「拿不到」；
本地有名册时**完全不联网**（断网可用）。

## 为什么要那个「BigInt 反面对照」

哈希是 32 字节 ⇒ hex 是 64 字符。**若浏览器端用 `BigInt(...).toString(16)`（不补零）**，
凡哈希以 `0` 开头（**约 1/16 ≈ 6.25%**）就会变成 63 字符 ⇒ 与 PG 的 64 字符**永远不相等**
⇒ 学生的删除请求永远匹配不上 ⇒ 页面只显示「权限不足」⇒ **静默失效，极难发现**。

线上 api.js 用的是 `('0' + a[i].toString(16)).slice(-2)` ⇒ **前导零保住** ⇒ 本项 ✅。
`tests.sql` 的第 13 条把这条**端到端**跑了一遍（库里存一个以 0 开头的哈希 → 本人带原始令牌 ⇒ 真删掉了）。

## 本轮实测结果（2026-10-24）

```
tests.sql      13/13 全过
hash_parity.js 浏览器 == PG：9/9 逐字相等；BigInt 变体在「哈希以 0 开头」上 64→63 ⇒ 确认该风险真实存在
```
