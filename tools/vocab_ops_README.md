# 教师侧「删一条 / 清空全部」搬到服务端（CloudBase `/vocab-ops`）

> 这份是**技术说明**（给会看代码的人）。给老师的大白话操作步骤在另外一页。

## 为什么要搬

听力记录站（学生端 + 教师看板）现在**都用公开的 anon key 直连 Supabase**，
所以 RLS 分不出「谁是老师」—— 任何拿到 anon key 的人都能删掉整个班的记录。

实际已发生：老师端「清空全部」按钮第一次真正生效（之前 RLS 挡着，是"假成功"），
一按就把 34 条真实听力记录删掉了。

⇒ 修法不是「再加一条 RLS」，而是**把老师的写操作挪到服务端**：
云函数用 **service_role** 执行删除，并且**在服务端校验老师口令摘要**
（复用现成的 `check_teacher_login`，不另造一套密码逻辑）。

## 文件

| 文件 | 作用 |
|---|---|
| `tools/vocab_ops.js` | 端点实现（模块化，可直接 require 进 ai-proxy 的 index.js） |
| `tools/test_vocab_ops.js` | 本地验收（对着 mock Supabase 真跑，29 条断言 + 5 组反向验证） |
| `tools/mock_supabase.js` | mock 的 Supabase（只实现用到的两个接口 + 一个数行数的探针） |
| `tools/listen_lock_anon_delete.sql` | **收尾 SQL**：把 anon 的整班删除彻底关掉（端点上线后再跑） |
| `tools/pg_lock_harness.sh` | 收尾 SQL 的**本地 PostgreSQL 真跑台**（11 条断言 + 4 组反向验证） |

## 契约（与前端一致）

```
POST {BASE}/vocab-ops
  { action:'ping' }
  { action:'delete_log', teacher_id, teacher_hash, id, class_key }
  { action:'clear_class', teacher_id, teacher_hash, class_key }

→ 200 { ok:true, deleted:N }        N = **真实删掉的行数**（不是"请求成功"）
→ 401 { ok:false, error:'unauthorized' }   校验不过 ⇒ 一行都不删
→ 400 { ok:false, error:'bad_request' | 'bad_class_key' | 'unknown_action' }
```

`teacher_hash` = `sha256('vocab-unit23-teacher-2026|' + 工号 + '|' + 密码)`，
和前端 `api.js` 里 `CFG.SALT` 的算法一致（`check_teacher_login` 也是这个口径）。

## 挂到现役 ai-proxy 上（老师做，四步）

🔴 **绝对不能新建函数** —— 会丢 HTTP 触发器 + 环境变量（MM_API_KEY 那次踩过）。
🔴 **上传 zip = 整份替换函数代码** ⇒ zip 里必须包含**现役 ai-proxy 的全部文件**，
   只放 `vocab_ops.js` 会把 `/ai/prompt` 整个打挂（NHK App 的 AI 讲解会失效）。

1. 拿到**现役线上那版** `index.js`（不是旧版）+ `package.json` + `scf_bootstrap`，
   把 `vocab_ops.js` 放进去。
2. 在 `index.js` 的主 handler 里加三行（**路径判断放在最前面**，别挂错）：

   ```js
   const { handleVocabOps } = require('./vocab_ops');
   // 主 handler 开头：
   if (String(event.path || '').indexOf('/vocab-ops') >= 0) return handleVocabOps(event);
   ```

3. 控制台加两个**环境变量**：

   | 变量 | 值 |
   |---|---|
   | `SB_URL` | `https://lszwxcmvcvbraqtrtgon.supabase.co` |
   | `SB_SERVICE_ROLE` | Supabase Dashboard → Settings → API → **service_role** |

   ⚠️ service_role 是**全库读写**的钥匙 ⇒ 只能放云端环境变量，
   **绝不能进前端 / 静态站 / APK / 任何 zip / 任何日志**。

4. 路由：控制台 HTTP 访问路径加 `/vocab-ops`（或 `tcb routes add`）。
   ⚠️ **别用 `tcb routes edit`**（会丢 FunctionType ⇒ 400 FUNCTIONS_PARAM_INVALID）⇒ 删了重加。
   ⚠️ zip 里要有 `scf_bootstrap`；控制台解压后可能丢可执行权限
   ⇒ 「函数配置 → 启动命令」改成 `bash scf_bootstrap`。

### 三个云函数专属的坑（都踩过）

| 坑 | 现象 | 修法 |
|---|---|---|
| 没有全局 `URL` | 本地全绿、线上 0.8 秒挂 | 自己拼字符串（`vocab_ops.js` 里已经这么写了） |
| `tcb routes edit` 丢 FunctionType | 400 FUNCTIONS_PARAM_INVALID | 删了重加 |
| 解压后丢可执行权限 | 启动失败 | 启动命令改 `bash scf_bootstrap` |

## 端点上线后的验收（两条，都要）

```bash
KEY=sb_publishable_3LuLZXWMadqYH3SLYOenig_ECHa51Mo
SU=https://lszwxcmvcvbraqtrtgon.supabase.co

# ① 拿 anon key 直接 curl 整班删 ⇒ 必须**被拒**（回读确认行还在，不能只看返回码）
curl -s -X DELETE "$SU/rest/v1/vocab_history_logs?class_key=eq.LISTEN" \
  -H "apikey: $KEY" -H "Authorization: Bearer $KEY" -H "Prefer: return=representation"
curl -s "$SU/rest/v1/vocab_history_logs?select=id&class_key=eq.LISTEN" \
  -H "apikey: $KEY" -H "Authorization: Bearer $KEY" -H "Prefer: count=exact" -i | grep -i content-range

# ② 拿 anon key 直接 curl 删别的班 ⇒ 必须被拒
curl -s -X DELETE "$SU/rest/v1/vocab_history_logs?class_key=eq.JR1" \
  -H "apikey: $KEY" -H "Authorization: Bearer $KEY" -H "Prefer: return=representation"
```

⇒ 两条都过了，再跑**收尾 SQL**（`tools/listen_lock_anon_delete.sql`），
把 anon 的整班删除彻底关掉。

## 收尾 SQL 干了什么

```sql
-- anon 能删的行只剩两类：
--   ① student_id = '__rls_probe__'   （验收脚本的探针行，任何班都行）
--   ② class_key = 'LISTEN' 且 student_id = 请求头 x-student-id
--      ⇒ **学生只能删自己那一条**；老师的「删一条/清空全部」已经不走这条路了
```

🔴 只删 `cmd='DELETE'` 的 policy ⇒ 老师的 `p_teach_all_*`（`FOR ALL TO authenticated`，
`cmd='ALL'`）**一个字都不动**。
🔴 收尾 SQL 里 DO 块先把 DELETE policy 全删光再重建 ⇒ 漏删一条宽松的 = 白收紧
（PERMISSIVE 之间是 OR）。**如果旧 policy 还在，CREATE 会同名报错、整笔回滚** ⇒ 失败得很响，不会半成功。

### 已知残留（诚实说明）

`x-student-id` 是**自称**的，能伪造 ⇒ 学生仍可能删掉同班同学的**单条**记录
（要逐条删、还要知道学号和行 id）。这一版比之前强的地方是**一键清空全班彻底没了**。
真正的隔离要靠"每人一个口令"（157 个学生发口令，代价大），先记着这条路。

## 本地怎么验

```bash
# 端点（29 条断言 + 5 组反向验证：改坏实现必须变红）
node tools/test_vocab_ops.js

# 收尾 SQL（11 条断言 + 4 组反向验证，在真的 PostgreSQL 上跑）
bash tools/pg_lock_harness.sh
```
