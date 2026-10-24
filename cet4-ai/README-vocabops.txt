============================================================
CloudBase ai-proxy 升级包（ CET4 AI 批改 + 教师端特权写端点 ）
============================================================

这个 zip 里**同时**包含两个功能，老师**只传这一个 zip** 就行：

  ① 四级 AI 批改     POST /ai/cet4        GET /ai/cet4-spec
  ② 教师端特权写端点 POST /vocab-ops       （删一条 / 清空全部）

⚠️ 为什么只传一个：
   之前有两个包（cet4 版 / vocab-ops 版）都是从同一份源码改出来的
   ⇒ 老师先后传两个，**后传的会把前传的覆盖掉**（另一个功能就没了）
   ⇒ 这个包是**在 cet4 版基础上再合并**的 ⇒ 一次传齐 ⇒ 两个都在

------------------------------------------------------------
【第 0 步 · 必做】在 CloudBase 控制台加两个环境变量
------------------------------------------------------------
函数名 `ai-proxy` → 「函数配置」→ 「环境变量」→ 新增：

  变量名            值
  SB_URL            https://lszwxcmvcvbraqtrtgon.supabase.co
  SB_SERVICE_ROLE   ← 老师自己的 Supabase service_role key

⚠️ SB_SERVICE_ROLE 从哪拿：
   Supabase Dashboard → 项目 lszwxcmvcvbraqtrtgon → 左侧「API Keys」
   ⇒ 取 **service_role** 那个（secret key，`sb_secret_...`；旧版是 `eyJ...` 开头那个也行）
   ⚠️ **这个 key 绝对不能写进代码、不能进 zip、不能发到群里** ⇒ 它绕过所有 RLS
   ⚠️ 没配这两个变量的话，vocab-ops 端点会**如实报 server_not_configured**（不会偷偷降级）

⚠️ **先加环境变量，再传 zip**（顺序反了也能跑，但先加更稳）

------------------------------------------------------------
【第 1 步】传包
------------------------------------------------------------
函数代码 → 上传 → 选 `ai-proxy-code-vocabops.zip` → 函数部署

⚠️ **上传 zip = 整份替换函数代码** ⇒ 所以 zip 里必须包含全部文件
   （本包含：index.js / package.json / scf_bootstrap / vocab_ops.js 四个）
⚠️ **绝对不要新建函数**（会丢 HTTP 触发器 + 环境变量）
⚠️ 若上传后函数起不来 ⇒「函数配置 → 启动命令」必须是 `bash scf_bootstrap`

------------------------------------------------------------
【第 2 步】四步验收（顺序不能变，第 1 步不过就立刻回滚）
------------------------------------------------------------
① GET /ai/prompt
   → prompt_sha 必须 == b62731f9d93d94fbb0cab505c6a1ae5f77bdd0b8407c7c7ca7123ecde8a66731
   → prompt_len 必须 == 1036
   ⇒ **不等 ⇒ 立刻传回滚包**（回滚包 = 只回退到「CET4 已生效、vocab-ops 未生效」的状态）

② GET /ai/cet4-spec
   → 200 + prompt_len 1839 + prompt_sha db277e82012675c992d61f71fffe67caa560e66735734dd7ff6a310617b8d937

③ POST /vocab-ops  {"action":"ping"}
   → 200 {"ok":true,"actions":["ping","delete_log","clear_class"]}

④ POST /ai/prompt 之外再随便打一个日语讲解，确认 AI 讲解没坏

------------------------------------------------------------
【第 3 步 · 等前端切完再跑】关掉匿名整班删除
------------------------------------------------------------
⚠️ **端点上线 ≠ 立刻收紧** ⇒ 前端没切过来之前跑了 SQL，学生的「删一条」会全失败。
   ⇒ 顺序：① 部署本包 ② 前端切到 /vocab-ops ③ 确认老师端在用新端点 ④ 再跑 SQL

   SQL：共享仓库 `tools/listen_lock_anon_delete.sql`
   本地真跑台：`tools/pg_lock_harness.sh`（11 条断言 + 4 组反向验证）

------------------------------------------------------------
【回滚】
------------------------------------------------------------
传 `ai-proxy-code-rollback.zip` 即可（3 个文件，index.js = cet4 版）
⇒ 恢复成「CET4 已生效、vocab-ops 未生效」
（回滚**不会**影响环境变量，也**不会**动已经跑过的 SQL ⇒ 若已跑 SQL，
  想恢复 anon 删除能力需要另外跑反向 SQL）

------------------------------------------------------------
【文件指纹（核对用）】
------------------------------------------------------------
部署包 ai-proxy-code-vocabops.zip
  index.js        sha256 2bce801152976d777184f26c57886f086e7871bee674117b63237fb5aedb6d8d
  vocab_ops.js   sha256 726589e0056bae51b0d4ca56a8c288d27dec24951006a1b6f051dc9713539f25
  package.json   sha256 879bc921bbb581d36c78a5b7777b20eea4a5213024632fbe8159ef7610347dc1
  scf_bootstrap  sha256 5dc3f57bb7a5f691c0969e73621f785a9b9dc5cfa8536aad85d8338f35dade69

回滚包 ai-proxy-code-rollback.zip
  index.js        sha256 e9d13ed875d4a89c2aef1f9b6de710519ef86f18e6fe2872f7ec8cd2c7bdc2ee
  package.json   sha256 879bc921bbb581d36c78a5b7777b20eea4a5213024632fbe8159ef7610347dc1
  scf_bootstrap  sha256 5dc3f57bb7a5f691c0969e73621f785a9b9dc5cfa8536aad85d8338f35dade69

------------------------------------------------------------
【路径说明】
------------------------------------------------------------
· 走**函数默认域名**（https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com）
  ⇒ /vocab-ops 和 /ai/vocab-ops **两个都通**
· 走**自定义域名 + 网关路由**
  ⇒ 网关只映射了 /ai 前缀 ⇒ **只能用 /ai/vocab-ops**
⇒ 保险起见前端用 **/ai/vocab-ops**（两种部署方式都成立）
