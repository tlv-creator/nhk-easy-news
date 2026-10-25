/* NHK 慢速新闻 —— 前端运行时配置
 *
 * 🔴 安全：这里**只能放公开可读的东西**（本文件会随站点一起公开，
 *    也会进 GitHub Pages 引导页的话更是全网可见）。
 *    MiniMax 的 Subscription Key / 任何 token **绝对不能写在这里**，
 *    泄露会被官方自动禁用。
 *
 * AI 讲解：中转地址。留空 = 未配置 → 「問 AI」按钮显示「AI 未配置」并禁用。
 * 填上之后整套逻辑不用改，只改这一个值就能切到真接口。
 * 契约见 手册.md「AI 讲解」一节。
 */
window.CFG = window.CFG || {};

/** 中转地址：POST 这个地址，body 见契约，期望返回 {ok:true, text:"..."} */
window.CFG.AI_ENDPOINT = 'https://vocab-test-d2gkep01q257d3351.service.tcloudbase.com/ai';

/** 可选：中转若开启校验才填 Bearer token。留空则不带 Authorization 头。 */
window.CFG.AI_TOKEN = '';

/** 剧名（发给模型时用，别写错） */
window.CFG.AI_TITLE = 'NHKやさしいことばニュース';

/** 🔴 音频基址：决定「音频从哪下载」
 *  · 留空 / []  ⇒ 用相对路径（本站自带 media/ 时用，行为与从前完全一致）
 *  · 填绝对 URL（字符串或数组）⇒ 媒体在别处、静态资源在本处
 *    · 数组 ⇒ 多个候选源，按顺序 fallback
 *    · 典型用途：静态站放 GitHub Pages（永久地址、地址永不失效），
 *      音频放 mcode 源站（60MB 音频塞不进 GitHub）
 *  · 本文件会公开 ⇒ 这里只能放公开可读的东西。 */
window.CFG.MEDIA_BASE = [];
