# meals-bot（userid-only @ 版）

## 文件
- `meals_bot.py`：主脚本（提醒不依赖飞书凭证；汇总调用飞书 Bitable 读表）。
- `.github/workflows/meals.yml`：定时 + 手动触发工作流（UTC）。

## 必填 Secrets
- `WECHAT_WEBHOOK`（企业微信群机器人完整 URL）
- `FORM_URL`（飞书表单链接；脚本会自动拼 `prefill_用餐日期=YYYY-MM-DD`）
- `FEISHU_APP_ID`、`FEISHU_APP_SECRET`
- `BITABLE_APP_TOKEN`（basc...）、`BITABLE_TABLE_ID`（tbl...）
- `MENTION_USERIDS`（如：`mr.Yu`；多个用逗号分隔）
- （可选）`LOCK_DATE`=`1` 锁定日期不可编辑

## 手动测试
Actions → `meals-bot` → **Run workflow**，选择：
- `remind_am`（或 `remind_pm`）→ 群里应收“表单入口（含今日 prefill）”；
- `report_lunch` / `report_dinner` / `report_breakfast` → 群里收汇总，并 @ `MENTION_USERIDS`。
