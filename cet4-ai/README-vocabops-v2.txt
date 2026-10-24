═══════════════════════════════════════════════════════════════════
  云函数部署包 v2（cet4 + vocab-ops）—— 2026-10-24
  文件：ai-proxy-code-vocabops-v2.zip
═══════════════════════════════════════════════════════════════════

🔴🔴 为什么发 v2（v1 别用了）：**v1 里的 vocab_ops.js 用了全局 fetch()**
   CloudBase 的 Node 沙箱（scf_bootstrap 起的那个 node）是 Node 12/16
   ⇒ **没有全局 fetch** ⇒ 函数能起来（部署"成功"）但 /vocab-ops 一调就 500
   ⇒ 证据：现役 ai-proxy 的 index.js 第 44-46 行用 require('https')，
           第 255 行还专门写着「绝对不要用 new URL()：沙箱里没有全局 URL」
   ⇒ 同一个沙箱不会凭空多出 fetch
   ⇒ v2 把 sbFetch 换成 https.request（Node 12+ 通用），**契约完全不变**

✅ v2 做过什么验证（不是"应该能跑"，是跑过）：
   ① index.js 与 v1 **逐字相同**（sha 2bce801152…）⇒ cet4 那边一个字没动
   ② vocab_ops.js 的**代码**里没有 fetch( / new URL(（用词法级剥注释后查的
      ⇒ 注释里写「不能用 fetch()」不算违规 ⇒ 不会假红）
   ③ node --check 两个文件都过；**解包后**再 check 一遍
   ④ 沙箱行为判据：**删掉 globalThis.URL 和 globalThis.fetch** 之后，
      端点仍然 ping → 200 {ok:true, service:'vocab-ops'}；真删一次 deleted=1
   ⑤ zip 权限复验：scf_bootstrap = 755、LF 换行、其余 644
   ⑥ 包内无真 key（占位符 sk-cp-xxxx 放过）

───────────────────────────────────────────────────────────────────
【老师操作顺序】一次只做一步，做完一步验一步

① 传这个 zip（**只传这一个**）
   CloudBase 控制台 → 环境 vocab-test-d2gkep01q257d3351（ap-shanghai）
   → 云函数 ai-proxy → 代码上传 → 选 ai-proxy-code-vocabops-v2.zip
   ⚠️ 传完**等它部署完成**（别急着传下一个）
   ⚠️ 传 zip 时**保留权限位**（scf_bootstrap 必须 755）

② 加两个环境变量（**服务端的**，前端和 zip 里都没有）
   SB_URL          = https://lszwxcmvcvbraqtrtgon.supabase.co
   SB_SERVICE_ROLE = Supabase Dashboard → Project Settings → API →
                     service_role 密钥（eyJ… 开头那串）
   🔴 **service_role 绝不能进前端 / 静态站 / APK / 任何 zip**
   🔴 这个 zip 里**没有**这两个值 ⇒ 老师自己填 ⇒ 填完才生效

③ 验端点（浏览器打开这两个地址，看 JSON）
   ping（应 200，带 service 标记）：
   https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/ai/vocab-ops
   ⇒ 浏览器直接 GET 会得到 405（只收 POST）⇒ **这是对的**，说明函数活着
   ⇒ 真验：让页面用 POST 打一次（老师端点「删一条」按钮就是）

④ 确认老师端按钮走的是**新端点**
   （前端已做：端点说 401 ⇒ 绝不退回 anon；端点没上线/网络不通 ⇒ 退回旧路）

⑤ 最后才跑**收尾 SQL**（这一步会关掉 anon 的整班删除口子）
   ⇒ 顺序不能反：端点没上线就跑 SQL ⇒ 老师的「删一条/清空全部」会直接废

───────────────────────────────────────────────────────────────────
【老师端按钮在「端点没上线」时的行为】（前端已实现）
   · 端点返回 401（口令不对）⇒ **绝不退回**（这是权限问题，退回也没用）
   · 端点没上线 / 网络不通 ⇒ **自动退回**旧的 anon 路径
   · 判断「端点在不在」靠**响应里的 service:'vocab-ops' 标记**，不靠 HTTP 码
     （ai-proxy 对任何 POST 都回 400 + {ok:false,error:'bad_request'}
      ⇒ 用状态码判断会把「没上线」误判成「上线但请求有问题」⇒ 按钮直接废）
═══════════════════════════════════════════════════════════════════
