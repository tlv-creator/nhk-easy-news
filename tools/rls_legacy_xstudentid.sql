-- =====================================================================
-- 【存档 · 已从公开页面删除】词汇站 2026-10-24 的「权限收紧（3 分钟）」旧方案
--   原始页面：vocab-fix-9f3a/index.html（曾在 GitHub Pages 上公开可访问，
--   内容含 x-student-id 限定版 policy + vocab_states using(true) 全开方案）
--   为什么删：
--     ① 它是 2026-10-24 造成「三个站全哑」的那份方案的**原文**（老师跑过）
--     ② 它公开可访问 ⇒ 任何人（包括学生）都能看到并照着再装一遍 ⇒ 防复发
--     ③ 新方案见 tools/fix_history_logs_rls.sql（class_key 白名单 + 开关）
--   下面是旧方案原文（仅留档，**不要执行**）
-- =====================================================================
CREATE POLICY</

DROP POLICY IF EXISTS p_anon_read_states  ON vocab_states;

DROP POLICY IF EXISTS p_anon_insert_states ON vocab_states;

DROP POLICY IF EXISTS p_anon_update_states ON vocab_states;

DROP POLICY IF EXISTS p_anon_insert_logs   ON vocab_history_logs;

DROP POLICY IF EXISTS p_anon_insert_online ON vocab_online_status;

DROP POLICY IF EXISTS p_anon_update_online ON vocab_online_status;

DROP POLICY IF EXISTS p_anon_delete_online ON vocab_online_status;

DROP POLICY IF EXISTS p_anon_insert_subs   ON vocab_submissions;

DROP POLICY IF EXISTS p_anon_update_subs   ON vocab_submissions;

DROP POLICY IF EXISTS p_teach_all_logs     ON vocab_history_logs;

DROP POLICY IF EXISTS p_teach_all_subs     ON vocab_submissions;

DROP POLICY IF EXISTS p_teach_all_online   ON vocab_online_status;

DROP POLICY IF EXISTS p_teach_all_states   ON vocab_states;

CREATE POLICY p_anon_read_states  ON vocab_states FOR SELECT TO anon USING (true);

CREATE POLICY p_anon_insert_states ON vocab_states FOR INSERT TO anon WITH CHECK (true);

CREATE POLICY p_anon_update_states ON vocab_states FOR UPDATE TO anon USING (true) WITH CHECK (true);

CREATE POLICY p_anon_insert_logs   ON vocab_history_logs FOR INSERT TO anon
  WITH CHECK (student_id = (current_setting('request.headers', true)::json->>'x-student-id'));

CREATE POLICY p_anon_insert_online ON vocab_online_status FOR INSERT TO anon
  WITH CHECK (student_id = (current_setting('request.headers', true)::json->>'x-student-id'));

CREATE POLICY p_anon_update_online ON vocab_online_status FOR UPDATE TO anon
  USING (student_id = (current_setting('request.headers', true)::json->>'x-student-id'))
  WITH CHECK (student_id = (current_setting('request.headers', true)::json->>'x-student-id'));

CREATE POLICY p_anon_delete_online ON vocab_online_status FOR DELETE TO anon
  USING (student_id = (current_setting('request.headers', true)::json->>'x-student-id'));

CREATE POLICY p_anon_insert_subs   ON vocab_submissions FOR INSERT TO anon
  WITH CHECK (student_id = (current_setting('request.headers', true)::json->>'x-student-id'));

CREATE POLICY p_anon_update_subs   ON vocab_submissions FOR UPDATE TO anon
  USING (student_id = (current_setting('request.headers', true)::json->>'x-student-id'))
  WITH CHECK (student_id = (current_setting('request.headers', true)::json->>'x-student-id'));

CREATE POLICY p_teach_all_logs   ON vocab_history_logs  FOR ALL TO authenticated USING (true) WITH CHECK (true);

CREATE POLICY p_teach_all_subs   ON vocab_submissions    FOR ALL TO authenticated USING (true) WITH CHECK (true);

CREATE POLICY p_teach_all_online ON vocab_online_status  FOR ALL TO authenticated USING (true) WITH CHECK (true);

CREATE POLICY p_teach_all_states ON vocab_states         FOR ALL TO authenticated USING (true) WITH CHECK (true);
