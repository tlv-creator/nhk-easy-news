#!/usr/bin/env node
/* 手工打包 APK（不走 Gradle）
 *
 *   aapt2 compile → aapt2 link(-A assets, -0 m4a 不压缩) → javac → d8 → aapt add classes.dex
 *   → 修 zip 本地文件头里的反斜杠 → zipalign → apksigner
 *
 * 本项目的特殊之处：
 *   1) **音频内置进 APK**（assets/audio/epNN.m4a，12 集 ~70MB），用 `-0 m4a` 让它不压缩存储
 *      —— 压缩了 AssetManager.openFd() 会抛「file is probably compressed」，内置音频就没法播。
 *   2) 包名 com.shun.study（自己的 keystore：keystore/shun.jks，口令 shun2026）
 *   3) 打包时排除 assets 里的 *.apk（别把自己打进自己）
 *
 * 环境变量（可选，有默认值）：
 *   ANDROID_SDK / ANDROID_SDK_ROOT / ANDROID_HOME
 *   BUILD_TOOLS_VERSION (默认 34.0.0)   PLATFORM (默认 android-34)
 *   JAVA_HOME                           KEYSTORE_PASS (默认 shun2026)
 */
'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFileSync, spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const APP_VERSION = "1.2.4";
// 🔴🔴 **用户手机里那把密钥的证书指纹**（换了它 ⇒ 用户装不上 ⇒ 收藏/进度全丢）
//   ⇒ 签名后必须验 APK 的证书就是这一把（NHK 推的 check_apk_signature.py）
const EXPECT_CERT_SHA1 = "9c04f344921d31fd7258a571e263b6e475e42f94";
const VERSION_CODE = "7";
const MIN_SDK = '24';
const TARGET_SDK = '34';
const KEYSTORE_PASS = process.env.KEYSTORE_PASS || 'shun2026';
const PKG = 'com.shun.study';
const DATA_DIR = process.env.SHUN_DATA || path.join(ROOT, 'web', 'data');
const AUDIO_DIR = process.env.SHUN_AUDIO || path.join(ROOT, 'audio');

function log(...a) { console.log(...a); }
function die(msg) { console.error('\n[FAIL] ' + msg + '\n'); process.exit(1); }

// ---------------------------------------------------------------- 找工具

function firstExisting(cands) {
  for (const c of cands) { if (c && fs.existsSync(c)) return c; }
  return null;
}
function findSdk() {
  const envs = [process.env.ANDROID_SDK, process.env.ANDROID_SDK_ROOT, process.env.ANDROID_HOME];
  const cands = [];
  for (const e of envs) if (e) cands.push(e);
  cands.push(path.join(os.homedir(), 'Android', 'Sdk'));
  cands.push('/opt/android-sdk', '/usr/lib/android-sdk');
  return firstExisting(cands);
}
function findBuildTools(sdk) {
  const want = process.env.BUILD_TOOLS_VERSION || '34.0.0';
  const dir = path.join(sdk, 'build-tools', want);
  const a2 = path.join(dir, 'aapt2');
  if (fs.existsSync(a2)) {
    return {
      dir,
      aapt2: a2, aapt: path.join(dir, 'aapt'), zipalign: path.join(dir, 'zipalign'),
      apksigner: path.join(dir, 'apksigner'), d8jar: path.join(dir, 'lib', 'd8.jar')
    };
  }
  const btRoot = path.join(sdk, 'build-tools');
  if (fs.existsSync(btRoot)) {
    const vers = fs.readdirSync(btRoot).sort().reverse();
    for (const v of vers) {
      const d = path.join(btRoot, v);
      const a = path.join(d, 'aapt2');
      if (fs.existsSync(a)) {
        return {
          dir: d, aapt2: a, aapt: path.join(d, 'aapt'), zipalign: path.join(d, 'zipalign'),
          apksigner: path.join(d, 'apksigner'), d8jar: path.join(d, 'lib', 'd8.jar')
        };
      }
    }
  }
  return null;
}
function findAndroidJar(sdk) {
  const plat = process.env.PLATFORM || 'android-34';
  const cands = [];
  if (sdk) cands.push(path.join(sdk, 'platforms', plat, 'android.jar'));
  cands.push('/opt/android-sdk/platforms/' + plat + '/android.jar');
  return firstExisting(cands);
}
function findJavaTool(name) {
  const jh = process.env.JAVA_HOME;
  const cands = [];
  if (jh) cands.push(path.join(jh, 'bin', name));
  // 🔴 **沙箱会重置 ⇒ /opt/jdk 会整个消失** ⇒ 必须先找工程内（NAS 上，不会被重置）那份
  //   （同款坑我已经踩过两次：tools/setup_android.sh 装出来的就是工程内这份）
  cands.push(path.join(ROOT, 'tools', 'jdk', 'bin', name));
  cands.push('/opt/jdk/bin/' + name, '/opt/jdk17/bin/' + name, '/usr/lib/jvm/' + name);
  const r = spawnSync('which', [name], { encoding: 'utf8' });
  if (r.status === 0) cands.unshift(r.stdout.split(/\r?\n/)[0].trim());
  return firstExisting(cands);
}
function findPython() {
  const r = spawnSync('which', ['python3'], { encoding: 'utf8' });
  if (r.status === 0) return r.stdout.split(/\r?\n/)[0].trim();
  return null;
}
function run(cmd, args, opts) {
  const o = Object.assign({ encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }, opts || {});
  const jh = process.env.JAVA_HOME || (fs.existsSync(path.join(ROOT, 'tools', 'jdk', 'bin', 'java'))
    ? path.join(ROOT, 'tools', 'jdk') : null);
  if (jh) {
    o.env = Object.assign({}, process.env, o.env || {});
    o.env.JAVA_HOME = jh;
    o.env.PATH = path.join(jh, 'bin') + path.delimiter + (o.env.PATH || '');
  }
  const r = spawnSync(cmd, args, o);
  if (r.status !== 0) {
    console.error((r.stdout || '') + (r.stderr || ''));
    die('命令失败: ' + cmd + ' ' + args.join(' '));
  }
  return r.stdout;
}

// ---------------------------------------------------------------- 前端体检

function preflight(appDir, audioList) {
  log('\n[1/8] 前端体检');
  const idx = path.join(appDir, 'index.html');
  if (!fs.existsSync(idx)) die('缺少 web/index.html');
  const js = path.join(appDir, 'app.js');
  if (!fs.existsSync(js)) die('缺少 web/app.js');
  const chk = spawnSync(process.execPath, ['--check', js], { encoding: 'utf8' });
  if (chk.status !== 0) die('app.js 语法错误：\n' + (chk.stderr || chk.stdout));

  const epIdx = path.join(DATA_DIR, 'eps', 'index.json');
  if (!fs.existsSync(epIdx)) die('缺少 data/eps/index.json（先跑 scripts/build_web_data.py）');
  const eps = JSON.parse(fs.readFileSync(epIdx, 'utf8')).episodes || [];
  if (!eps.length) die('index.json 里没有集数');
  for (const e of eps) {
    const f = path.join(DATA_DIR, 'eps', e.tag + '.json');
    if (!fs.existsSync(f)) die('缺少分集数据 ' + e.tag + '.json');
  }
  for (const need of ['lexicon/vocab.json', 'lexicon/styles.json', 'lexicon/grammar.json']) {
    if (!fs.existsSync(path.join(DATA_DIR, need))) die('缺少 data/' + need + '（先跑 scripts/build_lexicon.py）');
  }
  if (!audioList.length) die('audio/ 里没有 m4a');
  for (const a of audioList) {
    if (!fs.existsSync(a)) die('音频缺失：' + a);
  }
  log('  ✓ app.js 语法 / ' + eps.length + ' 集数据 / ' + audioList.length + ' 个音频');
  return eps.length;
}

// ---------------------------------------------------------------- 主流程

function main() {
  const sdk = findSdk();
  if (!sdk) die('找不到 Android SDK，请设置 ANDROID_SDK 环境变量');
  const bt = findBuildTools(sdk);
  if (!bt) die('找不到 build-tools');
  const androidJar = findAndroidJar(sdk);
  if (!androidJar) die('找不到 android.jar');

  const javac = findJavaTool('javac');
  const java = findJavaTool('java');
  const keytool = findJavaTool('keytool');
  if (!javac || !java) die('找不到 JDK（javac/java），请设置 JAVA_HOME');

  log('SDK        : ' + sdk);
  log('build-tools: ' + bt.dir);
  log('android.jar: ' + androidJar);
  log('javac      : ' + javac);

  const appDir = path.join(ROOT, 'web');
  const tplDir = path.join(ROOT, 'apk-template');
  if (!fs.existsSync(appDir)) die('缺少 web/ 目录');
  if (!fs.existsSync(tplDir)) die('缺少 apk-template/ 目录');

  const audioList = fs.readdirSync(AUDIO_DIR)
    .filter(f => f.toLowerCase().endsWith('.m4a'))
    .map(f => path.join(AUDIO_DIR, f));
  const epCount = preflight(appDir, audioList);

  // ---- 拷到纯 ASCII 临时目录（aapt2 处理不了中文路径）
  const tmp = path.join(os.tmpdir(), 'shun-build');
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  const stageApp = path.join(tmp, 'app');
  const stageTpl = path.join(tmp, 'apk-template');
  fs.cpSync(appDir, stageApp, { recursive: true });
  // 数据目录（可能和 web/data 不同源）
  fs.rmSync(path.join(stageApp, 'data'), { recursive: true, force: true });
  fs.cpSync(DATA_DIR, path.join(stageApp, 'data'), { recursive: true });
  // 内置音频
  fs.mkdirSync(path.join(stageApp, 'audio'), { recursive: true });
  for (const a of audioList) fs.copyFileSync(a, path.join(stageApp, 'audio', path.basename(a)));
  // 别把 APK 自己打进 assets（也不该有别的 .apk）
  (function rmApk(dir) {
    for (const f of fs.readdirSync(dir)) {
      const p = path.join(dir, f);
      if (fs.statSync(p).isDirectory()) rmApk(p);
      else if (f.toLowerCase().endsWith('.apk')) fs.rmSync(p, { force: true });
    }
  })(stageApp);
  fs.cpSync(tplDir, stageTpl, { recursive: true });

  /* 🔴 版本号三处必须一致：build 脚本 / manifest / Java 常量
   *    （MainActivity.APP_VERSION 只用于 ping() 诊断，对不上会让人一头雾水） */
  {
    const jv = path.join(ROOT, 'apk-template', 'java', 'com', 'shun', 'study', 'MainActivity.java');
    if (fs.existsSync(jv)) {
      const src = fs.readFileSync(jv, 'utf-8');
      const m = src.match(/APP_VERSION\s*=\s*"([^"]*)"/);
      if (!m) die('MainActivity.java 里找不到 APP_VERSION 常量');
      if (m[1] !== APP_VERSION) {
        die('版本号不一致：build=' + APP_VERSION + ' / MainActivity=' + m[1] + '（改一处不够）');
      }
      log('  ✓ 版本号一致：' + APP_VERSION + ' / vc' + VERSION_CODE);
    }
  }

  /* 🔴🔴 manifest 里 versionCode/versionName 是**显式写死**的，
   *    `aapt2 link --version-code` **盖不过** manifest 的值 → 产物永远是 1。
   *    必须在暂存后**改写 manifest 文本**（之前在 hub 栽过一次这个坑）。 */
  {
    const mf = path.join(stageTpl, 'AndroidManifest.xml');
    let mx = fs.readFileSync(mf, 'utf-8');
    const before = mx;
    mx = mx.replace(/android:versionCode="[^"]*"/, 'android:versionCode="' + VERSION_CODE + '"');
    mx = mx.replace(/android:versionName="[^"]*"/, 'android:versionName="' + APP_VERSION + '"');
    if (mx === before) die('manifest 里没找到 versionCode/versionName —— 改写失败，别发版');
    fs.writeFileSync(mf, mx);
    log('   manifest 版本改写 → versionCode ' + VERSION_CODE + ' / versionName ' + APP_VERSION);
  }
  /* 🔴 断言：AI 讲解的三个文件必须进包（漏了 = APK 里 AI 功能是哑的） */
  /* 🔴 墨水屏铁律闸门：**盒子边框**（四边）不能用纯黑/纯白 —— 电子墨水屏上会糊成粗杠杠。
   *    单边描边（border-bottom/left…）是结构线，实黑保留是对的。
   *    每次换 ai-client 都要重跑这一步；v1.5 就栽过一次（改了 css 却没同步进 client 目录）。
   *    → 用 scripts/check_eink_css.js（能解析 var() 变量链，字面量正则查不到）*/
  /* 🔴🔴 平台注入巡检（v8 工具）：**打进 APK 的网页**不能带平台的统计脚本/悬浮球
   *    —— ① 用户流量敏感（墨水屏机 + 移动网络）② 学习 App 产生外部打点不合适
   *    ③ 悬浮球在 WebView 里会挡底栏按钮。
   *    站上那份**要留**（平台每次部署都会注入，剥不掉）→ 这里只查**本地源**。
   *    源被工具链写回过脏版本（NHK 踩过）→ 这里提前拦住。*/
  {
    const strip = path.join(__dirname, 'apkchk', 'strip_platform_inject.py');
    if (fs.existsSync(strip)) {
      const r = spawnSync(findPython(), [strip, '--check', appDir], { encoding: 'utf-8' });
      if (r.status !== 0) {
        die('本地网页里混进了平台注入（统计脚本/悬浮球）—— 会耗用户流量、且悬浮球会挡按钮：\n' +
            (r.stdout || '') + (r.stderr || '') +
            '  → 修：python3 scripts/apkchk/strip_platform_inject.py web/');
      }
    }
  }
  {
    /* 🔴🔴 **必须把所有 css 一起传**（变量表跨文件合并）+ **html 也要传**
     *    （v4.3 起：只传 css → HTML 内联 <style> 里的违规完全扫不到 = 假绿灯；
     *      v4.3 还有个「相对路径假绿灯」的洞，只传 css 的用法结果不可信）。*/
    const cssFiles = ['ai-client.css', 'style.css', 'shell.css']
      .map(f => path.join(appDir, f)).filter(f => fs.existsSync(f));
    const htmlFiles = ['index.html', 'download.html']
      .map(f => path.join(appDir, f)).filter(f => fs.existsSync(f));
    const r = spawnSync(process.execPath,
      [path.join(__dirname, 'check_eink_css.js')].concat(htmlFiles).concat(cssFiles),
      { encoding: 'utf-8' });
    if (r.status !== 0) {
      die('墨水屏描边检查未通过（纯黑/纯白盒子描边会让墨水屏糊成粗杠杠）：\n' +
          (r.stdout || '') + (r.stderr || '') +
          '  → 自动修：node scripts/check_eink_css.js --fix web/*.css\n' +
          '  → 或换用已修好的 client（v1.6+）');
    }
  }

  ['ai-client.js', 'ai-client.css', 'shun-ai.js', 'shun-ai-ui.js', 'boot.js'].forEach(function (f) {
    if (!fs.existsSync(path.join(stageApp, f))) die('assets 缺 ' + f + '（APK 里 AI 讲解会不工作）');
  });
  log('  ✓ 暂存目录 ' + tmp + '（含 ' + audioList.length + ' 个内置音频 + AI 讲解四个文件）');

  const work = path.join(tmp, 'work');
  fs.mkdirSync(work, { recursive: true });

  // ---- [2] aapt2 compile
  log('\n[2/8] aapt2 compile（编译 res）');
  const resZip = path.join(work, 'res.zip');
  run(bt.aapt2, ['compile', '--dir', path.join(stageTpl, 'res'), '-o', resZip]);

  // ---- [3] aapt2 link（-A assets；-0 m4a → 音频不压缩，AssetManager.openFd 才可用）
  log('\n[3/8] aapt2 link（打包 assets + manifest，音频不压缩）');
  const baseApk = path.join(work, 'base.apk');
  run(bt.aapt2, [
    'link', '-o', baseApk,
    '-I', androidJar,
    '--manifest', path.join(stageTpl, 'AndroidManifest.xml'),
    '-A', stageApp,
    '-0', 'm4a',
    '--min-sdk-version', MIN_SDK,
    '--target-sdk-version', TARGET_SDK,
    '--version-code', VERSION_CODE,
    '--version-name', APP_VERSION,
    resZip
  ]);

  // ---- [4] javac
  log('\n[4/8] javac（编译 java）');
  const classesDir = path.join(work, 'classes');
  fs.mkdirSync(classesDir, { recursive: true });
  const javaFiles = [];
  (function walk(d) {
    for (const f of fs.readdirSync(d)) {
      const p = path.join(d, f);
      const st = fs.statSync(p);
      if (st.isDirectory()) walk(p);
      else if (f.endsWith('.java')) javaFiles.push(p);
    }
  })(path.join(stageTpl, 'java'));
  run(javac, ['-encoding', 'UTF-8', '-source', '8', '-target', '8', '-nowarn',
    '-classpath', androidJar, '-d', classesDir].concat(javaFiles));

  // ---- [5] d8
  log('\n[5/8] d8（转 dex）');
  const dexDir = path.join(work, 'dex');
  fs.mkdirSync(dexDir, { recursive: true });
  const classFiles = [];
  (function walk(d) {
    for (const f of fs.readdirSync(d)) {
      const p = path.join(d, f);
      const st = fs.statSync(p);
      if (st.isDirectory()) walk(p);
      else if (f.endsWith('.class')) classFiles.push(p);
    }
  })(classesDir);
  run(java, ['-cp', bt.d8jar, 'com.android.tools.r8.D8', '--min-api', MIN_SDK,
    '--lib', androidJar, '--output', dexDir].concat(classFiles));

  // ---- [6] aapt add classes.dex
  log('\n[6/8] aapt add classes.dex');
  const dexFile = path.join(dexDir, 'classes.dex');
  if (!fs.existsSync(dexFile)) die('classes.dex 没生成');
  run(bt.aapt, ['add', '-f', baseApk, 'classes.dex'], { cwd: dexDir });

  // ---- [6.5] 修 zip 路径（Windows 上 aapt2 会写反斜杠 → 白屏）
  log('\n[6.5/8] 修 zip 本地文件头');
  const fixer = path.join(ROOT, 'scripts', 'fix_apk_paths.py');
  if (fs.existsSync(fixer)) {
    const py = findPython();
    if (py) run(py, [fixer, baseApk]);
  }

  // 🔴🔴 **交付前最后一道闸门：工程目录里不许有签名私钥**
  //   （会被 website_deploy 的「私有源码 ZIP」打包带走 ⇒ 能签出可覆盖安装的 APK = 交出用户的更新权）
  //   顺带：这道闸门也保证「密钥在工程外」这件事不会悄悄退化
  {
    const checker = path.join(ROOT, 'scripts', 'check_no_secret_in_project.py');
    if (!fs.existsSync(checker)) {
      die('❌ 缺 scripts/check_no_secret_in_project.py ⇒ 不许出包（不许跳过放行）');
    }
    // 🔴🔴 **必须取退出码**：`run()` 返回的是 **stdout 字符串** ⇒ 我第一版写 `if (r !== 0)`
    //   ⇒ 任何非空输出都会被当成「失败」⇒ **假红灯**（闸门是绿的，却被我拦下一个好包）
    //   ⇒ **通则：函数的返回值语义要从实现确认，不能假设**（同「拿 probe() 的键去断言 rows 的键」那族）
    const r = spawnSync('python3', [checker, ROOT], { encoding: 'utf8' });
    if (r.status !== 0) {
      console.error((r.stdout || '') + (r.stderr || ''));
      die('❌ 工程目录里有签名私钥 ⇒ 拒绝出包（见上面的清单）');
    }
    log('  ✓ 工程目录里没有签名私钥/凭据');
  }

  // ---- [7] zipalign
  log('\n[7/8] zipalign');
  const aligned = path.join(work, 'aligned.apk');
  run(bt.zipalign, ['-f', '-p', '4', baseApk, aligned]);

  // ---- [8] 签名
  log('\n[8/8] apksigner 签名');
  // 签名密钥**放在工程目录之外**（/workspace/keys/shun.jks）——
  // 这样 website_deploy 打包「私有源码 ZIP」时不会把能签出覆盖安装 APK 的密钥带走。
  // 🔴🔴🔴 **绝不自动生成新密钥**（2026-10-23 我自己审出来的洞）：
  //   换一把密钥 ⇒ 用户手机上的旧 App **装不上**（签名不一致 ⇒ 覆盖安装失败）
  //   ⇒ 用户的收藏/进度/设置全丢 ⇒ 比「出不了包」严重得多
  //   ⇒ **「找不到 ⇒ 必须 FAIL，不许静默兜底」**（同「SITE_URL=PLACEHOLDER 就 exit 1」那条）
  //   ⇒ 也**绝不能把密钥放进工程目录**（会被私有源码 ZIP 带走）
  const ksCandidates = [
    process.env.SHUN_KEYSTORE,
    '/workspace/keys/shun.jks'
  ].filter(Boolean);
  const ks = ksCandidates.find(f => fs.existsSync(f));
  if (!ks) {
    die('❌ 找不到原签名密钥（找过：' + ksCandidates.join(' / ') + '）\n'
      + '   ⇒ **绝不自动生成新密钥**：换了密钥用户就装不上（覆盖安装失败，收藏/进度全丢）\n'
      + '   ⇒ 修法：把**原来那把** shun.jks 放回 /workspace/keys/（口令 ' + KEYSTORE_PASS + '）\n'
      + '   ⇒ 证书指纹必须是 9c04f344921d31fd7258a571e263b6e475e42f94');
  }
  log('  签名密钥：' + ks + '（工程目录之外 ⇒ 私有源码 ZIP 带不走它 ✅）');
  const outDir = path.join(ROOT, 'output');
  fs.mkdirSync(outDir, { recursive: true });
  const outApk = path.join(outDir, 'shun.apk');
  const signedAscii = path.join(work, 'signed.apk');
  run(bt.apksigner, ['sign', '--ks', ks, '--ks-pass', 'pass:' + KEYSTORE_PASS,
    '--key-pass', 'pass:' + KEYSTORE_PASS,
    '--v1-signing-enabled', 'true', '--v2-signing-enabled', 'true', '--v3-signing-enabled', 'true',
    '--out', signedAscii, aligned]);
  const cert = run(bt.apksigner, ['verify', '--print-certs', signedAscii]);
  log(cert.trim());

  // 🔴🔴🔴 **签名指纹闸门**（2026-10-23 NHK 推，我采纳）：
  //   「密钥文件在」≠「签出来的是用户手机里那把」⇒ 必须验 **APK 里的证书**
  //   ⇒ 换一把 = 用户装不上（覆盖安装被拒）⇒ 收藏/进度全丢，而现场看不出来
  {
    const sigChk = path.join(ROOT, 'scripts', 'apkchk', 'check_apk_signature.py');
    if (!fs.existsSync(sigChk)) die('❌ 缺 scripts/apkchk/check_apk_signature.py ⇒ 不许出包（不许跳过放行）');
    const r = spawnSync('python3', [sigChk, '--apk', outApk, '--expect-sha1', EXPECT_CERT_SHA1],
      { encoding: 'utf8' });
    if (r.status !== 0) {
      console.error((r.stdout || '') + (r.stderr || ''));
      die('❌ 签名证书不是期望的那把（期望 ' + EXPECT_CERT_SHA1 + '）⇒ 拒绝出包');
    }
  }
  fs.copyFileSync(signedAscii, outApk);

  // 🔴🔴 **闸门：APK 里写死的每个 mcode 域名都必须在受保护清单里**
  //   起因（2026-10-24 实测踩到）：换音频站时改了引导页，**忘了改 APK 内的
  //   AUDIO_BASE_LOCAL** ⇒ 引导页正常时完全正常，**引导页拉不到时音频兜底指向旧站**
  //   ⇒ 而旧站没有 ep13 ⇒ Ep278 没声音，且**现场看不出来**
  //   ⇒ 这道闸门把「换站要改所有引用方」变成**机器可查的**
  //   （必须放在**签名之后**：signed.apk 这时才存在）
  {
    const checker = path.join(ROOT, 'scripts', 'check_apk_domains.py');
    if (!fs.existsSync(checker)) {
      die('❌ 缺 scripts/check_apk_domains.py ⇒ 不许出包（不许跳过放行）');
    }
    const r = spawnSync('python3', [checker, '--apk', signedAscii],
                        { encoding: 'utf8', timeout: 180000 });
    if (r.status !== 0) {
      console.error((r.stdout || '') + (r.stderr || ''));
      die('❌ APK 里写死的域名没有全部登记进 protected-sites.json ⇒ 拒绝出包');
    }
    log('  ✓ APK 内写死域名全部在受保护清单里');
  }

  // ---- 收尾校验
  log('\n[校验] aapt list（Android 的口径，必须全正斜杠）');
  const list = spawnSync(bt.aapt, ['list', signedAscii], { encoding: 'utf8' });
  if (list.status !== 0) die('aapt list 失败：' + (list.stderr || ''));
  const lines = list.stdout.split(/\r?\n/);
  const bad = lines.filter((l) => /\\/.test(l));
  const assets = lines.filter((l) => l.startsWith('assets/'));
  log('  assets 条目 ' + assets.length);
  if (bad.length) {
    log('  ✗ 发现 ' + bad.length + ' 条反斜杠路径');
    die('zip 路径没修干净，别装！');
  }
  if (!lines.includes('classes.dex')) die('APK 里没有 classes.dex');
  for (const need of ['assets/index.html', 'assets/app.js', 'assets/style.css',
    'assets/shell.js', 'assets/shell.css', 'assets/data/eps/index.json',
    'assets/data/lexicon/vocab.json']) {
    if (!assets.includes(need)) die('APK 里没有 ' + need);
  }
  const auds = assets.filter((l) => l.startsWith('assets/audio/'));
  if (auds.length !== audioList.length) {
    die('内置音频数量不对：' + auds.length + '（期望 ' + audioList.length + '）');
  }
  if (assets.some((l) => l.toLowerCase().endsWith('.apk'))) die('assets 里有 apk（别把自己打进自己）');

  // 音频必须是 STORED（不压缩）—— 压缩了 openFd() 会抛「file is probably compressed」
  const py = findPython();
  if (py) {
    const check = path.join(ROOT, 'scripts', 'check_apk_audio.py');
    if (fs.existsSync(check)) run(py, [check, signedAscii]);
  }

  /* 🔴🔴 APK 级 API 等级闸门（工具：peer 的 apk_check_api.py，纯 Python 零依赖）
   *    查「引用了 minSdk(=24) 之外、又没有 Build.VERSION.SDK_INT 守卫」的方法
   *    → 那种调用在 Android 7 上就是 NoSuchMethodError / NoClassDefFoundError → 打开 App 秒退。
   *    ⚠️ catch (Throwable) **不是**版本守卫：老设备只是安静地少一段功能，用户以为 App 有 bug。
   *
   *    三条铁律：
   *      ① 出包后、出 zip 前跑
   *      ② jar 缺失 / sha256 校验不过 → **die**（不许「跳过放行」= 假绿灯）
   *      ③ jar 不进发布包（220MB 会撑爆 source archive 的 200MB 上限）→ 只留 .py + 拉取脚本
   */
  {
    const chkDir = path.join(__dirname, 'apkchk');
    const jarDir = path.join(chkDir, 'jars');
    const fetch = path.join(chkDir, 'fetch_android_jars.py');
    const py = findPython();
    if (!py) die('找不到 python3 —— 跑不了 API 等级闸门，拒绝出包');
    if (!fs.existsSync(fetch)) {
      die('缺 scripts/apkchk/fetch_android_jars.py —— API 闸门没有判据来源，拒绝出包');
    }
    /* 🔴 官方 jar 的校验走**权威脚本**：sha256 + **不依赖 sha 的结构自检**
     *    （合法 zip / 有 .class 条目 / class magic == ca fe ba be）
     *    —— sha 只能证明「跟固化时一样」，固化时拿到的就可能是坏文件。
     *    jar 缺失或校验不过 → **die**（不许「跳过放行」= 假绿灯）。*/
    log('\n[API 闸门] 校验官方 android.jar（sha256 + 结构自检）');
    const v = spawnSync(py, [fetch, '--out', jarDir, '--verify'],
      { encoding: 'utf-8', maxBuffer: 16 * 1024 * 1024 });
    log((v.stdout || '').trim());
    if (v.status !== 0) {
      die('官方 android.jar 校验失败 —— jar 是闸门的**判据来源**，坏了结论整个反过来（比没检查更坏）');
    }
    const apiChk = path.join(chkDir, 'apk_check_api.py');
    if (!fs.existsSync(apiChk)) die('缺 scripts/apkchk/apk_check_api.py —— 拒绝出包');
    const jarArgs = [];
    [24, 26, 29].forEach(function (api) {
      const j = path.join(jarDir, 'android-' + api + '.jar');
      if (!fs.existsSync(j)) die('缺 android.jar：' + j + ' —— 拒绝出包');
      jarArgs.push('--jars', api + '=' + j);
    });
    log('[API 闸门] 扫 ' + path.basename(outApk) + '（minSdk ' + MIN_SDK + '）');
    const r = spawnSync(py, [apiChk, signedAscii].concat(jarArgs),
      { encoding: 'utf-8', maxBuffer: 64 * 1024 * 1024 });
    log((r.stdout || '').trim());
    if (r.status !== 0) {
      die('API 等级闸门不通过 —— APK 里有在老系统上会直接崩的调用，别出包：\n' +
          (r.stdout || '') + (r.stderr || ''));
    }
    log('  ✓ API 等级闸门通过');
  }

  /* 🔴 交付必须打成 .apk.zip —— 手机浏览器直接下 .apk 会被拦（微信内置浏览器尤其），
   *    用户得先解压再安装。zip 里用 ASCII 文件名（shun.apk），中文名个别安卓解压/安装器会出幺蛾子。*/
  {
    const zpath = path.join(outDir, 'shun.apk.zip');
    if (fs.existsSync(zpath)) fs.unlinkSync(zpath);
    const z = spawnSync('zip', ['-q', '-j', '-9', zpath, outApk], { encoding: 'utf-8' });
    if (z.status !== 0) die('打 zip 失败：' + (z.stderr || ''));
    // 完整交付包：APK + 使用说明（**不含源码/脚本/素材/keystore**）
    // 🔴🔴 **zip 里的文件名一律用 ASCII** —— 中文名在 Windows 解压会变问号/乱码
    //    （我第一版直接 zip 中文名，peer 在 Windows 上打开是乱码 ⇒ 规矩写进脚本）
    const DOCS = [
      ['Japanese with Shun 精读-使用说明.md', 'README-JP-STUDY.md'],
      ['交付说明.md', 'README-CN-DELIVERY.md']
    ];
    const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'shun-docs-'));
    const staged = [];
    for (const pair of DOCS) {
      const s = path.join(outDir, pair[0]);
      if (!fs.existsSync(s)) continue;
      const d = path.join(stage, pair[1]);
      fs.copyFileSync(s, d);
      staged.push(d);
    }
    const full = path.join(outDir, 'shun-study-' + APP_VERSION + '.zip');
    if (fs.existsSync(full)) fs.unlinkSync(full);
    const z2 = spawnSync('zip', ['-q', '-j', '-9', full, outApk].concat(staged),
      { encoding: 'utf-8' });
    try { fs.rmSync(stage, { recursive: true, force: true }); } catch (e) { }
    if (z2.status !== 0) die('打完整交付 zip 失败：' + (z2.stderr || ''));
    log('  ✓ 交付 zip：' + path.basename(zpath) + '（' +
        (fs.statSync(zpath).size / 1048576).toFixed(2) + ' MB）· ' +
        path.basename(full) + '（含使用说明）');
  }

  const sz = fs.statSync(outApk).size;
  log('\n✅ 打包完成：' + outApk);
  log('   大小 ' + (sz / 1048576).toFixed(2) + ' MB   versionCode ' + VERSION_CODE + ' / versionName ' + APP_VERSION);
  log('   包名 ' + PKG + '   内置音频 ' + auds.length + ' 集 / ' + epCount + ' 集字幕');
}

main();
