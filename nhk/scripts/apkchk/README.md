# APK 静态检查器 v8.3（统一分发点）

一个包，六件套：

| 文件 | 干什么 |
|---|---|
| `apk_check_api.py` | **API 兼容性闸门**：扫 APK 里的框架调用，看有没有被 `if (SDK_INT >= N)` 正确包住 |
| `apk_check_formats.py` | **格式串闸门**：`getString(R.string.x, "有 %s 占位符")` 这类错位（会「点图标秒退」） |
| `test_apk_check_api.py` | API 闸门的**自检**（正反两向）—— 不跑自检就等于没验证过工具 |
| `fetch_android_jars.py` | 自动下载/提取/校验**官方 android.jar**（API 24/26/29/34） |
| `make_guard_sample.py` | 现场造一个**最小守卫样本 APK**（约 900 B）—— 用来验证闸门本身没坏 |
| `guard_sample.apk` | 内置的最小样本（**9 个类**：两种守卫写法 + 阴性对照 + import + 纯隔离类安全/不安全两对 + 阈值不够） |
| `strip_platform_inject.py` | **剥平台注入**：统计脚本 `data-minimax-analytics` + 悬浮球 `#minimax-floating-ball`（打进 APK assets 的网页用） |

---

## 🔴 旧地址已作废

| 旧地址 | 状态 |
|---|---|
| `https://leh9lk4211d3v.space.mcode.cn` | **已删除**（曾被误删，后由 pony 重建） |
| `https://1s25bmxqpjqpj.space.mcode.cn` | pony 重建版，**内容与本包逐字节一致**；本包为唯一权威源 |

**v7 各文件 md5（可直接对拍）：**
```
apk_check_api.py 2e2bcea4bc54be0fc92fe5a2df548f2d   ← v7 改过（纯隔离类 + 调用图）
apk_check_formats.py ac49a10c00037d41830b11b86374a340   ← **从 v4 起就没变过**（共享文件，刻意不动）
test_apk_check_api.py 81f0121375080d5fcbebbe7e7d2152d9   ← v7 改过（新增反向 E）
fetch_android_jars.py 64f4a278561fa3c649e05a0b5f5f1664
make_guard_sample.py 3122285435279da0a5df5e92aaad449f
mirror_css.py 03065ce16b454db7221a4b2758f5e7e6   ← 已对齐 NHK 的 patch 行为（无参数/-h 打印用法）
guard_sample.apk 52d0054fb5a51b427019ca0bba875c8e   ← 1151 B，8 个类
```

---

## 一、API 兼容性闸门

### 🔴 v6 新增：两种守卫写法**都**认（Shun 2026-10-23 提出）

`if (SDK_INT >= O) { … }` 和 `if (SDK_INT < O) return null;` 编译出来是**两种不同指令**：

| 源码写法 | javac 产物 | 指令 | 语义 |
|---|---|---|---|
| `if (SDK_INT >= O) { … }` | 跳到 **else** 侧 | **`if-lt`** (0x34) | 「不满足就跳过新 API」 |
| `if (SDK_INT < O) return null;` | 跳到 **else** 侧 | **`if-ge`** (0x35) | 「不满足就提前返回」 |

两者**格式都是 22t、语义相反** —— 只认一种就会把另一种**误判成「无守卫」**。

🔴 **本工具两种都认**（`apk_check_api.py` 第 191 行的 `IF_OPS` 同时收 0x34 / 0x35，
第 276–279 行对两种跳向分别算「新分支区间」）。

**字节级实证（真实 APK，不是推理）** —— 用 hub v1.12 的真 dex：
```
它的 10 个守卫里，**if-ge 形式有 3 处**（op=0x35）、if-lt 有 4 处（op=0x34）：
  PlaybackService$Api26.builder          op=53 (if-ge)  阈值 26
  PlaybackService$Api26.ensureChannel    op=53 (if-ge)  阈值 26
  PlaybackService.ensureChannel          op=53 (if-ge)  阈值 26
  PlaybackService.buildNotif             op=52 (if-lt)  阈值 26
  PlaybackService.ensureStarted          op=52 (if-lt)  阈值 26
  MainActivity.webViewVersion            op=52 (if-lt)  阈值 26
  ...

【实验 1】只把 `Api26.builder` 的 if-ge 指令改成 nop（外层调用点的守卫还在）
  → OK（框架方法 161 / 框架类 57 全在 API 24 内）
  ⇒ **这是正确的**：外层 `PlaybackService.buildNotif` 的调用点仍在守卫分支里，
    老设备根本走不到 `Api26.builder` → 拆掉内层守卫并不影响安全性。

【实验 2】再把调用方 `PlaybackService.buildNotif` 的守卫也拆掉（两层都断）
  → FAIL 「❌ 无守卫 —— 同方法里找不到 SDK_INT 判断，且该方法存在**不受守卫**的调用点」
  ⇒ 这一处 if-ge **确实被当作有效守卫在用**，不是碰巧被别处覆盖。
```
🔴 **注意**：实验 1 说明 v7 的判据看的是「**实际安全**」而不是「形式合规」——
隔离类里写没写守卫**本身**不决定安全与否，**调用点有没有守卫**才决定。
（v6 没有这条兜底，实验 1 会误报「无守卫」→ 假红灯卡住出包。）

🔴 **自检已固化成用例**（`test_apk_check_api.py` 零参数即可跑）：
```
[selftest] 样本里守卫分布：if-lt 2 个 / if-ge 1 个
[selftest] PASS  正向 · 原样（有守卫）
[selftest] PASS  反向 A · 拆掉全部 3 个守卫
[selftest] PASS  反向 B · 全部阈值 → 21
[selftest] PASS  反向 C · 只拆 **if-ge** 形式守卫（`if (SDK_INT < N) return null`）
[selftest] PASS  反向 D · 只拆 **if-lt** 形式守卫（`if (SDK_INT >= N) {…}`）
```
（反向 C / D 是关键：**单独拆一种**才能证明「两种都认」；
只拆「全部」的话，拆完都是无守卫，**证明不了是哪一种被认**。）

### 🔴 v7 新增：**纯隔离类**形态也认（Android 官方推荐写法）

`if (SDK_INT >= O) { … }` 和 `if (SDK_INT < O) return null;` 编译出来是**两种不同指令**：

### 🔴 v7 新增：官方推荐的「纯隔离类」写法也认

Android 官方（AndroidX `compat.md`）原文：
> Non-reflective calls to new APIs gated on `SDK_INT` **must** be made from
> **version-specific static inner classes** to avoid **verification errors**…
> This is enforced at build time by the **`ClassVerificationFailure`** lint check.

**「verification errors」是关键**：Android 的 verifier 在**类加载/链接阶段**就检查方法里的类型引用 ——
引用了本机不存在的类/方法，**在方法被执行之前**就可能抛 `VerifyError` / `NoClassDefFoundError`。
⇒ 方法体内那句 `if (SDK_INT >= 26)` **救不了它**（炸在验证阶段，不是执行阶段）。

⇒ 官方推荐写法 = **把新 API 调用放进版本特定的静态内部类，方法体内不重复写守卫**。

🔴 **v6 之前这一种会被误判成「❌ 无守卫」**（假红灯，白白卡住出包）——
因为旧判据只看「方法自己体内有没有 SDK_INT 判断」。

**v7 的判据（不需要递归，很简单）**：
> 一个新 API 引用安全，如果：
> ① 它所在的方法**自己体内有守卫**；**或**
> ② 它所在的方法**所有调用点都落在守卫分支里**
>    —— 那样老设备永远走不到这个方法体，调用者自己怎么被调用**完全无关**。

⚠️ **② 有个边界**：如果一个方法**一个调用点都找不到**（顶层入口 / `@JavascriptInterface`），
就**不能**用 ② 放行（无法证明「老设备走不到」）→ 保守判不安全。

🔴 **官方还提到第二个坑（比第一个更容易踩）**：**隐式 cast**。
即使隔离了，若方法**返回新类型**、在外部被隐式转成老类型，仍会验证失败
（新类型在低版本设备上被当成 `Object`，`Object` 不能隐式当 `Drawable` 用）。
⇒ **在隔离类内部就把 cast 做完**，对外只暴露 `Object` 或老类型。

**实证**（`guard_sample.apk` 里现成有这两个类）：
```
✅ GuardIsoSafe    纯隔离类形态：方法内无守卫，但所有调用点都在守卫分支里
❌ GuardIsoUnsafe  同形态，但被**无守卫**的地方调用 → 判不安全
```
反向用例「反向 E」：**只拆掉调用点的守卫** → 纯隔离类必须立刻变不安全 ✅

### 🔴🔴 v7.1：**扫描统计永远打印** + `--list-guarded`（防「全过 vs 没扫到」）

🔴 **老问题（Shun 2026-10-23 点出）**：
> checker 判「全过」时，「**真扫到并判有守卫**」和「**压根没扫到**」**输出长得一样** → 无法对账。

**v7.1 的解法（两处）**：

**① 扫描统计永远打印**（不用加参数）：
```
[apk-api] 扫描统计：代码项 241 个 / invoke 调用点 1309 处 / 类型引用 181 处
                  / 需检查的框架方法 161 个 / 需检查的框架类 57 个
```
⇒ 「压根没扫到」= 这几个数字**异常小** → 一眼能看出来。
（例如「框架类 0 个 / 需检查的框架方法 0 个」就说明**根本没扫到东西**，不是没问题。）

🔴 **v8.2 起这行还带 `min-api`**，并附一条警告：
```
[apk-api] 扫描统计：代码项 241 个 / invoke 调用点 1309 处 / 类型引用 181 处
                  / 需检查的框架方法 161 个 / 需检查的框架类 57 个  (min-api 24)
         ⚠️ 阈值 **≤ min-api** 的守卫会被 d8 **常量折叠**掉，字节码里根本不存在
            → 不会出现在上面的统计里
```
**为什么必须带上 min-api**（NHK 2026-10-23 指出）：
**任何靠「扫分支指令」统计守卫数量的工具，在阈值 ≤ min-api 时都会少数** ——
因为 d8 把那些分支优化掉了，**字节码里压根没有**。
⇒ 读守卫数量时必须知道 min-api，否则会把「少数」误读成「没有」。

**①′ v8.3 新增：结构化汇总行**（给脚本读）
```
[apk-api] SUMMARY ok=0 methods=7 classes=2 unguarded=2 threshold=1 oldbranch=0
```
🔴 **别用「子串 count」当判据** —— 提示文案里也会出现「无守卫」「阈值不够」这些词
→ `out.count('无守卫')` 会把**提示文案**一起数进去。
**我踩过：实际 2 条无守卫，count 出来 5 条** → 自检基线直接失真。
⇒ **要读计数就读这行 SUMMARY**（自检已经改读它）。

**② `--list-guarded`**：OK 时也把「被判为已守卫」的清单逐条打出来（类级 + 方法级 + 调用点 + 守卫阈值）：
```bash
python3 apk_check_api.py app.apk --list-guarded --jars 24=... --jars 26=...
```
```
   ── 已被版本守卫包住（安全，仅供参考）8 条 ──
   Landroid/app/NotificationChannel;
       ☠️ 类缺失，API 26 才引入 —— 但引用都在 `if (SDK_INT >= 26)` 分支内 ✅
   Landroid/app/Notification$Builder;.<init>(Landroid/content/Context;Ljava/lang/String;)V
       ⚠️ API 26 才引入
       调用点: Lcom/jpstudy/hub/PlaybackService$Api26;
       ✅ 被 `if (SDK_INT >= 26)` 的**新 API 分支**包着
```
⇒ **不需要再写外部 `inspect_guards.py`**，任何环境都能对账。

### 🔴🔴 「隔离了就一定绿」是**误解** —— 隔离类≠安全

（Shun 2026-10-23 提醒，已写进这里）
判据 ② 要求「**所有**调用点都在守卫分支里」。所以：
- 隔离类里**有一个方法**被某处「无守卫地」调用（忘了的调用点 / 反射 / 从别处进）
  → **那一处会判不安全** ✅
- 隔离类的方法**一个调用点都找不到**（顶层入口 / `@JavascriptInterface`）
  → **不能**用 ② 放行（证不出来就保守）→ 判不安全 ✅

⇒ **「隔离」只是必要条件之一，不是充分条件。**
真正的判据是「**老设备能不能走到这个方法体**」。

### 🔴 关于 `import` 放文件顶部
- `import android.app.NotificationChannel;` 只是**编译期**行为，**不产生任何运行时引用** → 安全。
- 真正会炸的是**字节码里的引用**（`new-instance` / `invoke-*`）出现在哪个类、哪个方法、
  是否落在 API 守卫分支内 —— 这正是本工具判的东西。
- 「隔离到独立内部类」是**额外保险**（老设备根本不加载那个类），**不是必须**。
  方法内守卫就够了；本工具按**指令所在方法**判定，与隔离与否无关。

### 为什么需要
Android 的新 API 在老设备上会 **`NoSuchMethodError` 闪退**。典型：
`WebView.getCurrentWebViewPackage()`（API 26）在 Android 7.x 上不存在。

🔴 **try/catch 不是版本守卫** —— 有 try/catch 只会「静默少一段信息」，
用户看到的是「App 好像有 bug」，而不是明确的崩溃。

### 用法
```bash
python3 apk_check_api.py app.apk --jars 24=android-24.jar --jars 26=android-26.jar \
                               --jars 29=android-29.jar --jars 34=android-34.jar
```

### 判定（指令级 CFG，不靠「类里有没有 SDK_INT」）
| 输出 | 含义 |
|---|---|
| `✅ 被 if (SDK_INT>=N) 新分支包着` | 安全 |
| `❌ 落在旧分支` | 危险：该调用在老系统上会执行 |
| `❌ 守卫阈值不够(N<M)` | 危险：判断的是 API N，但这个调用要 API M |
| `❌ 无守卫` | 危险 |

- 沿**继承链**查方法（javac 发的是限定类型，方法可能定义在父类）
- 豁免按「**同一方法的字节码区间**」判，不按「类里有没有 SDK_INT」

### 自检（**必须先跑**）
```bash
python3 test_apk_check_api.py <某个.apk> --jars 24=... --jars 26=... --jars 29=... --jars 34=...
```
- **正向**：APK 原样 → 必须判 OK
- **反向 A**：拆掉**全部**守卫 → 必须判「无守卫」
- **反向 B**：全部阈值改成 21 → 必须判「阈值不够」

🔴 **只做单向会被恒绿实现骗过**（一个永远返回 OK 的实现能过正向）。
🔴 退出码 0 = 自检全过；1 = 工具不可信，不能拿去当闸门。

---

## 二、格式串闸门

```bash
python3 apk_check_formats.py app.apk
```
扫 `getString(R.string.x, ...)` 的**占位符个数**与实参数个数是否对得上。
对不上 → 运行到那行会 `Resources.NotFoundException`（用户看到「点某个图标就闪退」）。

---

## 三、官方 android.jar（`fetch_android_jars.py`）

```bash
python3 fetch_android_jars.py --out scripts/apkchk/jars
python3 fetch_android_jars.py --verify --out scripts/apkchk/jars
```

### 三个必须记住的坑
1. 🔴 **不要用 `unzip` 解 jar** —— 旧版 unzip 会误报
   `invalid zip file with overlapped components (possible zip bomb)`。
   本工具用 Python `zipfile` **精确取 entry**。
2. 🔴 **sha256 固化值必须跟 jar 同目录**（`jars/jars.sha256`），
   而且**必须从已通过结构自检的文件上计算**（先验结构，再算 sha）。
3. 🔴 **不能用「jar 里必须有 `android/app/NotificationChannel.class`」当结构判据** ——
   它 API 26 才存在，API 24 的好 jar 会被误判成坏。

### 本包内置的官方 jar 固化值（API 24/26/29/34）
```
API 24  4c438bf1b3d4e6104faccc62e1ffe95ef197b88055497b540a871349e93c8685
API 26  cdc1846376a14b0370cc63454a129606b4a52cc50ada75ef0d4cf956b1ad2daa
API 29  d4ce0daae1695abc7497009c61237f774f52c6ffdc222784e03f60effb917532
API 34  6cea1df3efb77103ac3e2beb9bf4718964b0e0869ab16d39d29d5cbae1c147ad
```

### `--verify` 做什么
**两道独立证据**：sha256 对上 **且** 结构自检通过。任一不过 → FAIL。

---

## 四、接进 build 的位置

```
[7/8] apksigner 校验
      ↓
[8/8] API 闸门（apk_check_api.py） ← 必须在打副本/发布之前
      ↓
      打包
```

🔴 **jar 缺失就 die**，不许「jar 不在就跳过闸门」—— 那是假绿灯。
🔴 **jar 不要进发布包**（体积大 + 没必要）。

---

## 五、写在最后的三条纪律

1. 🔴 **工具的 bug 会直接变成「我的结论」** —— 工具报问题后，先用**已知正确/已知错误**的样本反验工具，再动代码。
2. 🔴 **自检必须正反两向** —— 只测单向会被恒绿实现骗过。
3. 🔴 **「崩溃」≠「测试红」** —— 断言自检有效性时要看**收尾汇总行**，不能只看子进程退出码。

---

## 🔴 v8 新增：`strip_platform_inject.py`（剥平台注入）

### 它能做什么 / 不能做什么（2026-10-23 NHK 实测后定的位）
- ✅ **能做**：① 剥**打进 APK assets** 的网页里的注入（hub 那种：网页打进 assets 自己加载）
              ② 洗**本地源文件**被工具链写回的污染
- ❌ **做不到**：让**站上**那份没有注入 —— 平台**每次部署都会重新追加**，
              「上传前剥离」对站上那份**无效**（NHK 实测：手工删过，部署后又回来）。
  ⇒ **纯 WebView App（APK 里没有 assets）不需要这个工具**。

### 为什么要剥
1. **隐私**：统计脚本会 POST 到 `agent.minimax.cn/…/website-traffic/page-views`，
   payload 带 `node_id` / `session_id` / `biz_id` → 学习 App 产生外部打点。
2. **挡界面**：悬浮球会盖住底栏按钮，而且点它会 `window.open('agent.minimax.cn')` **跳出 App 打开浏览器**。

### 用法
```bash
python3 strip_platform_inject.py <文件或目录>            # 剥
python3 strip_platform_inject.py <路径> --dry-run        # 只看会改什么
python3 strip_platform_inject.py <路径> --check          # **只查不剥**，有残留 exit 1
python3 strip_platform_inject.py <app.apk> --check-apk    # **直接扫 APK**（不解压、不落盘）
```
⇒ `--check` 可以**单独用**（当 build 卡点 / 源文件污染巡检），不一定要先剥。
⇒ `--check-apk` 直接读 APK 里的 `assets/**/*.html` → **不用先解到临时目录**，
   适合「每次 build 都要跑」的场景；纯 WebView App（APK 里没 html）会直接说「没有 html ✅」。

### 🔴 「无守卫」这个报错**会把人往错方向引** —— 现在会附带提示
`if (SDK_INT >= N)` 里 **N ≤ min-api** 时，**d8 会常量折叠掉整个 if 分支** →
字节码里根本看不到那个判断 → checker 只能报「**无守卫**」，而**不是**「阈值不够」。

⇒ **一个写错的守卫（`if (SDK_INT >= 21)` 调 API 26）在 d8 里会被整个优化掉**，
   源码看着有守卫、编译产物里没有 → **报错信息会误导人往错的方向查**。

⇒ v8.1 起，「无守卫」的报错里**自带这条提示**：
```
💡 若源码里写了 `if (SDK_INT >= N)` 而 **N ≤ min-api**，d8 会**常量折叠**掉整个 if 分支
   → 字节码里根本看不到那个判断 → 这里就会报「无守卫」而不是「阈值不够」。
   （看到这条先核对源码阈值：那种守卫本来就是无效的，d8 帮你删了。）
```

### 🔴🔴 v8.3：自检本身也加固了 ——「靠改坏来验证的测试，必须先断言确实改坏了」

（NHK 2026-10-23 提出，我当晚就自查补上）

**背景**：自检的反向用例是「把 dex 里的守卫字节改坏 → 必须报无守卫」。
🔴 **但如果「改坏」这一步其实没生效**（偏移写错、字符串没匹配上、目标串带了引号）→
**反向用例就是假的** —— 它会「通过」，而实际上什么都没测。

⇒ v8.3 的 `patch_apk` 改成：
1. **逐个偏移比对 before/after**，没变就 **raise AssertionError**（「改坏没生效，反向用例就是假的」）
2. **改完重算 dex 头**（sha1 + adler32）⇒ 反例 APK 是**合法 dex**
   （不重算的话 dex 头是坏的 → 真机 ART 拒绝加载 → 自检在测一个「现实里不存在的东西」）
3. 自检新增一条用例：**所有反例 APK 的 dex 头必须自洽** ✅

🔴 **「反向验证失败」的第一反应不该是「断言没用」，而是「我的『改坏』到底生效了吗」。**
（NHK 第一次做这个反向验证时，目标串带了首尾单引号 → 没匹配上 → 长度变化 0 = 什么都没改。）

### 🔴 两个真坑（我自己踩的，贴出来当反面教材）
**① 非贪婪正则剥嵌套 HTML → 留孤儿节点**
悬浮球内部**还有嵌套 div**（`minimax-ball-content` / `minimax-close-icon`），
用 `.*?</div>` 会在**第一个** `</div>` 停 → **留下孤儿节点**（页面里一个孤零零的「×」）。
⇒ 改成 div **配对匹配**（数 `<div`/`</div>` 深度），**配不上就保守不动**。
⚠️ **如果我没做「剥完再扫一遍」，我会直接说「剥干净了」** —— 这个洞和「工具的 bug 会直接变成结论」同源。

**② d8 会把 `if (SDK_INT >= N)`（N ≤ min-api）常量折叠掉**
造「守卫阈值不够」的样本时，阈值 **≤ min-api 会被 d8 优化没**：
```
SDK_INT >= 21、min-api 24  →  恒真  →  整个 if 分支消失  →  字节码里根本没守卫
```
⇒ 测「阈值不够」必须用 **阈值 > min-api**（样本里用 25，min-api 24 → 25 < 26 → 正好触发）。
⚠️ 我第一版写 21，字节码里 `new-instance` 直接裸奔，**差点以为是 checker 的锅**。

### 实测（hub v1.12 的 4 个真实文件）
```
ama        14878 → 8371 字节  (-6507)   剥 10 处
dora       12490 → 5983 字节  (-6507)   剥 10 处
tadoku     15428 → 8921 字节  (-6507)   剥 10 处
wakakusa   11109 → 8575 字节  (-2534)   剥  9 处
```
四道验证全过：**标签配平** ✅ / **只删了该删的**（被删片段全部含注入特征，非注入内容 0 段）✅ /
**幂等**（剥两次一致）✅ / **浏览器实跑**（剥后 JS 错误**变少** —— 注入脚本在 `file://` 下本来就在报错）✅

---

## 🔴 v7 踩到的一个可复用坑：dex 的 `method_idx` 是**差值**，而且**两个列表各自独立**

要建「谁调用了谁」的调用图，就必须知道每个 `code_item` 属于**哪个方法**。
dex 的 `class_data_item` 里，方法索引是 **`method_idx_diff`（差值）**，不是绝对索引；
而且 **`direct_methods` 和 `virtual_methods` 是两个独立列表**，
**各自的第一个元素是绝对索引**，之后的才是差值。

🔴 **写错的后果不是报错，是「静默取到别人的方法名」**：
延续累加会让 `virtual[0]` 算成别的类的 `<init>` → 方法名全错位 →
调用图建错 → 判定结论跟着错。

**⇒ 验证办法（别靠肉眼）**：把两种算法都算一遍，拿 `method_info()` 反查
**所属类是不是当前类**，一眼就能看出哪个对。
（本次就是用这个办法暴力验证出来的。）

⚠️ 这跟之前那次「`judgeColors(m[3])` 写成 `m[4]`」是**同一种病**：
**两份实现逐字对拍 / 反查交叉验证，是发现索引错位的唯一手段。**
