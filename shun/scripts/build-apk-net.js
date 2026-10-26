#!/usr/bin/env node
/* Shun 播客 · 日中对照精读 —— **网络版** APK 构建（不走 Gradle）
 *
 * 与仓库那份 shun/scripts/build-apk.js 的区别（**故意不共用**）：
 *   那份是「内置版」专用，强依赖：
 *     · audio/ 里的 m4a（内置音频）
 *     · data/eps/*.json（内置 50 集数据）
 *     · web/ 里的 AI 讲解四件套（ai-client.js / shun-ai.js / shun-ai-ui.js / boot.js）
 *     · 墨水屏描边闸门 scripts/check_eink_css.js
 *   ⇒ 网络版**什么都不内置** ⇒ 那些断言全部不适用 ⇒ 硬套会到处 die
 *   ⇒ 本脚本是**独立的一份**，不碰那份
 *
 * 流程：aapt2 compile → aapt2 link → javac → d8 → aapt add classes.dex
 *       → 修 zip 本地文件头反斜杠 → zipalign → apksigner
 *
 * 环境变量：
 *   ANDROID_SDK / ANDROID_SDK_ROOT / ANDROID_HOME
 *   BUILD_TOOLS_VERSION (默认 34.0.0)   PLATFORM (默认 android-35)
 *   JAVA_HOME                           KEYSTORE_PASS (默认 shun2026)
 *   KEYSTORE (默认 <root>/keystore/shun.jks)
 *   EXPECT_CERT_SHA1 (默认 9c04f344921d31fd7258a571e263b6e475e42f94)
 *   APP_VERSION / VERSION_CODE          OUT_APK
 */
'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFileSync, spawnSync } = require('child_process');

const ROOT = __dirname;
// 🔴 模板目录：本地开发布局是 apkbuild/apk-template，仓库布局是 scripts/../apk-template-net
//    （peer 实测：只写死一个 ⇒ 在仓库布局下 fs.cpSync 直接 ENOENT）
const TPL_CANDIDATES = [
  process.env.TPL_DIR,
  path.join(ROOT, 'apk-template'),
  path.join(ROOT, 'apk-template-net'),
  path.join(ROOT, '..', 'apk-template-net'),
].filter(Boolean);
const TPL = TPL_CANDIDATES.find(p => fs.existsSync(p) && fs.statSync(p).isDirectory());
if (!TPL) {
  console.error('❌ 找不到原生层模板目录。试过：\n' +
    TPL_CANDIDATES.map(p => '     ' + p).join('\n') +
    '\n  ⇒ 用 TPL_DIR=<目录> 显式指定');
  process.exit(1);
}
const APP_VERSION = process.env.APP_VERSION || '1.3.0';
const VERSION_CODE = process.env.VERSION_CODE || '8';
const MIN_SDK = '24';
const TARGET_SDK = '34';
const PKG = 'com.shun.study';
const KEYSTORE = process.env.KEYSTORE || path.join(ROOT, 'keystore', 'shun.jks');
const KEYSTORE_PASS = process.env.KEYSTORE_PASS || 'shun2026';
// 🔴 用户手机里那把密钥的证书指纹（换了 ⇒ 用户装不上 ⇒ 收藏/进度全丢）
const EXPECT_CERT_SHA1 = process.env.EXPECT_CERT_SHA1 || '9c04f344921d31fd7258a571e263b6e475e42f94';
const OUT_APK = process.env.OUT_APK || path.join(ROOT, 'out', 'shun-study-net.apk');

function log(...a) { console.log(...a); }
function die(msg) { console.error('\n[FAIL] ' + msg + '\n'); process.exit(1); }
function firstExisting(c) { for (const x of c) if (x && fs.existsSync(x)) return x; return null; }

function findSdk() {
  const envs = [process.env.ANDROID_SDK, process.env.ANDROID_SDK_ROOT, process.env.ANDROID_HOME];
  const c = [];
  for (const e of envs) if (e) c.push(e);
  c.push(path.join(os.homedir(), 'Android', 'Sdk'), '/opt/android-sdk', '/usr/lib/android-sdk');
  return firstExisting(c);
}
function findBuildTools(sdk) {
  const root = path.join(sdk, 'build-tools');
  if (!fs.existsSync(root)) return null;
  const vers = fs.readdirSync(root).sort().reverse();
  const want = process.env.BUILD_TOOLS_VERSION || '34.0.0';
  for (const v of vers) {
    const d = path.join(root, v);
    if (!fs.existsSync(path.join(d, 'aapt2'))) continue;
    if (v !== want && vers.indexOf(want) >= 0) continue;
    return {
      dir: d, aapt2: path.join(d, 'aapt2'), aapt: path.join(d, 'aapt'),
      zipalign: path.join(d, 'zipalign'), apksigner: path.join(d, 'apksigner'),
      d8jar: path.join(d, 'lib', 'd8.jar'), version: v
    };
  }
  return null;
}
function findAndroidJar(sdk) {
  const plats = path.join(sdk, 'platforms');
  if (!fs.existsSync(plats)) return null;
  const want = process.env.PLATFORM || 'android-35';
  const vers = fs.readdirSync(plats).sort().reverse();
  const order = [want].concat(vers);
  for (const v of order) {
    const p = path.join(plats, v, 'android.jar');
    if (fs.existsSync(p)) return p;
  }
  return null;
}
function findJavaTool(name) {
  const c = [path.join(process.env.JAVA_HOME || '', 'bin', name)];
  c.push('/usr/bin/' + name, '/usr/local/bin/' + name);
  if (process.env.JAVA_HOME) c.unshift(path.join(process.env.JAVA_HOME, 'bin', name));
  return firstExisting(c);
}
function run(cmd, args, opts) {
  const o = Object.assign({ encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }, opts || {});
  const r = spawnSync(cmd, args, o);
  if (r.status !== 0) {
    console.error((r.stdout || '') + (r.stderr || ''));
    die('命令失败: ' + cmd + ' ' + args.join(' '));
  }
  return r.stdout;
}

function main() {
  const sdk = findSdk();
  if (!sdk) die('找不到 Android SDK（设 ANDROID_SDK）');
  const bt = findBuildTools(sdk);
  if (!bt) die('找不到 build-tools');
  const androidJar = findAndroidJar(sdk);
  if (!androidJar) die('找不到 android.jar');
  const javac = findJavaTool('javac');
  const java = findJavaTool('java');
  const keytool = findJavaTool('keytool');
  if (!javac || !java) die('找不到 JDK（javac/java），设 JAVA_HOME');

  log('SDK        : ' + sdk);
  log('build-tools: ' + bt.dir + ' (' + bt.version + ')');
  log('android.jar: ' + androidJar);
  log('javac      : ' + javac);

  // ---- [1] 暂存（纯 ASCII 路径，aapt2 处理不了中文路径）
  const tmp = path.join(os.tmpdir(), 'shun-net-build');
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  const stage = path.join(tmp, 'tpl');
  fs.cpSync(TPL, stage, { recursive: true });

  // 🔴 版本号三处一致：build / manifest / MainActivity
  {
    const jv = path.join(stage, 'java', 'com', 'shun', 'study', 'MainActivity.java');
    if (!fs.existsSync(jv)) die('缺少 MainActivity.java');
    const src = fs.readFileSync(jv, 'utf-8');
    const m = src.match(/APP_VERSION\s*=\s*"([^"]*)"/);
    if (!m) die('MainActivity.java 里找不到 APP_VERSION');
    if (m[1] !== APP_VERSION) die('版本号不一致：build=' + APP_VERSION + ' / MainActivity=' + m[1]);
    const mf = path.join(stage, 'AndroidManifest.xml');
    let mx = fs.readFileSync(mf, 'utf-8');
    // 🔴 判「正则有没有匹配上」，**不能**判「文本有没有变」
    //    （值本来就等于目标时替换后文本不变 ⇒ 会被误判成「没找到」）
    const reC = /android:versionCode="[^"]*"/;
    const reN = /android:versionName="[^"]*"/;
    if (!reC.test(mx)) die('manifest 里没找到 android:versionCode');
    if (!reN.test(mx)) die('manifest 里没找到 android:versionName');
    mx = mx.replace(reC, 'android:versionCode="' + VERSION_CODE + '"');
    mx = mx.replace(reN, 'android:versionName="' + APP_VERSION + '"');
    fs.writeFileSync(mf, mx);
    log('  ✓ 版本号一致：' + APP_VERSION + ' / vc' + VERSION_CODE);
  }

  const work = path.join(tmp, 'work');
  fs.mkdirSync(work, { recursive: true });

  // ---- [2] aapt2 compile
  log('\n[2/8] aapt2 compile');
  const resZip = path.join(work, 'res.zip');
  run(bt.aapt2, ['compile', '--dir', path.join(stage, 'res'), '-o', resZip]);

  // ---- [3] aapt2 link（网络版：**无 assets**）
  log('\n[3/8] aapt2 link（无 assets：网络版不内置任何资源）');
  const baseApk = path.join(work, 'base.apk');
  run(bt.aapt2, [
    'link', '-o', baseApk,
    '-I', androidJar,
    '--manifest', path.join(stage, 'AndroidManifest.xml'),
    '--min-sdk-version', MIN_SDK,
    '--target-sdk-version', TARGET_SDK,
    '--version-code', VERSION_CODE,
    '--version-name', APP_VERSION,
    resZip
  ]);
  log('  ✓ base.apk ' + fs.statSync(baseApk).size + ' 字节');

  // ---- [4] javac
  log('\n[4/8] javac');
  const classes = path.join(work, 'classes');
  fs.mkdirSync(classes, { recursive: true });
  const jfiles = [];
  (function walk(d) {
    for (const f of fs.readdirSync(d)) {
      const p = path.join(d, f);
      if (fs.statSync(p).isDirectory()) walk(p);
      else if (f.endsWith('.java')) jfiles.push(p);
    }
  })(path.join(stage, 'java'));
  if (!jfiles.length) die('没有 .java 源文件');
  run(javac, ['-source', '8', '-target', '8', '-encoding', 'UTF-8',
    '-bootclasspath', androidJar, '-classpath', androidJar,
    '-d', classes].concat(jfiles));
  if (!fs.existsSync(path.join(classes, 'com', 'shun', 'study', 'MainActivity.class')))
    die('javac 没产出 MainActivity.class');
  log('  ✓ 编译 ' + jfiles.length + ' 个源文件');

  // ---- [5] d8 → classes.dex
  log('\n[5/8] d8');
  // 🔴 d8 **不接受目录**当 program input（会报 "Unsupported source file type"）
  //    ⇒ 必须逐个传 .class 文件，并给 --lib android.jar
  const dexDir = path.join(work, 'dex');
  fs.mkdirSync(dexDir, { recursive: true });
  const classFiles = [];
  (function walk(d) {
    for (const f of fs.readdirSync(d)) {
      const p = path.join(d, f);
      if (fs.statSync(p).isDirectory()) walk(p);
      else if (f.endsWith('.class')) classFiles.push(p);
    }
  })(classes);
  if (!classFiles.length) die('没有 .class 文件');
  run(java, ['-cp', bt.d8jar, 'com.android.tools.r8.D8',
    '--min-api', MIN_SDK, '--lib', androidJar, '--output', dexDir].concat(classFiles));
  const dex = path.join(dexDir, 'classes.dex');
  if (!fs.existsSync(dex)) die('d8 没产出 classes.dex');
  log('  ✓ classes.dex ' + fs.statSync(dex).size + ' 字节');

  // ---- [6] aapt add classes.dex
  log('\n[6/8] aapt add classes.dex');
  const unsigned = path.join(work, 'unsigned.apk');
  fs.copyFileSync(baseApk, unsigned);
  run(bt.aapt, ['add', '-f', unsigned, 'classes.dex'], { cwd: dexDir });

  // ---- 修 zip 本地文件头里的反斜杠（aapt add 会写成 Windows 路径）
  log('\n[6b/8] 修 zip 本地文件头');
  const zf = path.join(work, 'fixzip.py');
  fs.writeFileSync(zf, [
    'import zipfile, sys, os',
    'p = sys.argv[1]',
    'zin = zipfile.ZipFile(p, "r")',
    'items = zin.infolist()',
    'data = {i.filename: zin.read(i) for i in items}',
    'zin.close()',
    'zo = zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED)',
    'for i in items:',
    '    zi = zipfile.ZipInfo(i.filename, date_time=i.date_time)',
    '    zi.compress_type = i.compress_type',
    '    zi.external_attr = i.external_attr',
    '    zo.writestr(zi, data[i.filename])',
    'zo.close()',
    'print("fixed", len(items))'
  ].join('\n'));
  run('python3', [zf, unsigned]);
  log('  ✓ zip 头已修');

  // ---- [7] zipalign
  log('\n[7/8] zipalign');
  const aligned = path.join(work, 'aligned.apk');
  run(bt.zipalign, ['-p', '-f', '4', unsigned, aligned]);
  log('  ✓ aligned ' + fs.statSync(aligned).size + ' 字节');

  // ---- [8] 签名
  log('\n[8/8] apksigner');
  if (!fs.existsSync(KEYSTORE)) {
    die('找不到签名密钥: ' + KEYSTORE + '\n' +
        '  ⇒ **必须是用户手机里那把**（证书 ' + EXPECT_CERT_SHA1 + '）\n' +
        '  ⇒ 换密钥 = 用户装不上 = 收藏/进度全丢\n' +
        '  ⇒ 绝不自动生成一把新的');
  }
  const signed = path.join(work, 'signed.apk');
  // 🔴 别名大小写：Java KeyStore 的别名**大小写不敏感**，但 apksigner 匹配失败会报
  //    "alias not found" ⇒ 依次试，先成功者为准（peer 实测：小写 shun / 大写 SHUN 都能碰上，
  //    不同 keystore 存的写法不一样 ⇒ 别写死一个）。
  const aliasCandidates = (process.env.KS_ALIAS ? [process.env.KS_ALIAS] : [])
    .concat(['SHUN', 'shun']);
  let signErr = null, usedAlias = null;
  for (const al of aliasCandidates) {
    try {
      run(bt.apksigner, ['sign', '--ks', KEYSTORE, '--ks-pass', 'pass:' + KEYSTORE_PASS,
        '--ks-key-alias', al, '--out', signed, aligned]);
      usedAlias = al;
      signErr = null;
      break;
    } catch (e) {
      signErr = e;
    }
  }
  if (!usedAlias) {
    die('签名失败（试过别名: ' + aliasCandidates.join(', ') + '）\n' + (signErr || ''));
  }
  log('  ✓ 用别名签名: ' + usedAlias);
  // 验证书指纹（签完立刻验，不要只信 apksigner 退出码）
  const v = run(bt.apksigner, ['verify', '--print-certs', signed]);
  const m = v.match(/SHA-?1[^:]*:\s*([0-9a-fA-F:]+)/);
  const got = m ? m[1].replace(/:/g, '').toLowerCase() : '';
  log('  证书 SHA-1: ' + got);
  if (got !== EXPECT_CERT_SHA1.toLowerCase()) {
    die('证书指纹不对：期望 ' + EXPECT_CERT_SHA1 + ' 实际 ' + got +
        '\n  ⇒ 换了密钥 ⇒ 用户装不上 ⇒ 收藏/进度全丢 ⇒ 拒绝出包');
  }
  log('  ✅ 证书指纹与用户手机里那把一致');

  // ---- 输出
  fs.mkdirSync(path.dirname(OUT_APK), { recursive: true });
  fs.copyFileSync(signed, OUT_APK);
  log('\n✅ 完成: ' + OUT_APK + '（' + fs.statSync(OUT_APK).size + ' 字节）');
  log('   包名 ' + PKG + ' / versionCode ' + VERSION_CODE + ' / versionName ' + APP_VERSION);
}

main();
