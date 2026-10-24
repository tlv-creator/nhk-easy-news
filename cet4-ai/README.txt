四级精读日报 · AI 批改（中转新增分支）部署包
===========================================

【这是什么】
给现役的 CloudBase 中转函数 ai-proxy 增加「四级翻译批改」端点。
**不新建函数**（新建会丢 HTTP 触发器和环境变量 MM_API_KEY）。
**不动** /ai、/ai/prompt、/ai/quota 的任何一行（日语那套逐字未变，prompt 指纹已验）。

【怎么部署（老师）】
1. 下载 ai-proxy-code-cet4.zip
2. CloudBase 控制台 → 云函数 → ai-proxy → 「代码」→ 上传 → 选这个 zip → 部署
3. 若函数起不来：函数配置 → 启动命令 → 改成 `bash scf_bootstrap`
   （zip 解压后 scf_bootstrap 可能丢可执行权限 —— CloudBase 老坑）

【部署后必须按顺序验这一遍】
1) GET  https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/ai/prompt
   → prompt_sha 必须 == b62731f9d93d94fbb0cab505c6a1ae5f77bdd0b8407c7c7ca7123ecde8a66731
   → prompt_len 必须 == 1036
   （**这一步过了才说明没碰坏日语那套**；不等就立刻回滚）
2) GET  .../ai/cet4-spec   → 应返回四级 prompt + 自己的 sha
3) GET  .../ai/audio-config → 应返回 v3 真配置（现役缺这条，合并后顺带修好）
4) POST .../ai/cet4        → 应返回 ok:true + result（定档/分数/逐句改法）

【任何一步不过 ⇒ 上传 ai-proxy-code-rollback-cet4.zip】
回滚包里的 index.js 就是合并前那份本身（sha 79d5b1bd…），传回即恢复原状。

【新增的两个端点】
POST /ai/cet4
  body: {mode:"2"|"5", sentences:[{no,zh,reference,points}], student:[{no,text}]}
  → {ok:true, result:{band,band_name,score_range,score,confidence,summary,
                     sentences:[{no,student,reference,missing_points,
                                 grammar:[{type,span,fix,note}],revised,comment}],
                     error_types:{}, weakest_points:[]}}
  → {ok:true, unparsed:true, raw:"…"}   ← 模型没按 JSON 输出时的降级
GET  /ai/cet4-spec
  → {ok:true, prompt, prompt_sha, prompt_len, types, bands}

【指纹】
部署包 index.js  sha256 e9d13ed875d4a89c2aef1f9b6de710519ef86f18e6fe2872f7ec8cd2c7bdc2ee
回滚包 index.js  sha256 79d5b1bd67331202f29b542b9a0747127ec96c9d5cc4d69b23f7738dbb1c7e38
package.json / scf_bootstrap 两个包里逐字相同，未改。
