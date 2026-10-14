# 05 Alerts and reports

**For:** operators enabling email delivery, and users scheduling reports.

A **report** emails a screenshot of a chart or dashboard on a schedule. An **alert**
runs a SQL condition on a schedule and emails when it is met.

## Enabling them (operators)

The feature flags, Celery schedule and screenshot settings are already in the shared
config (see [03 Configuration](03-configuration.md)). What a stack still needs:

1. **SMTP.** Fill in the SMTP block in your `<env-file>` — see "Email (SMTP)" in
   [03](03-configuration.md). Never in `docker/.env`, which is committed.
2. **Workers running.** Reports are produced by `superset-worker` and scheduled by
   `superset-worker-beat`. Both start with the rest of the stack; check with
   `docker compose -f <compose-file> ps`.
3. **Recreate after config changes:**

   ```bash
   docker compose -f <compose-file> up -d superset superset-worker superset-worker-beat
   ```

4. **Confirm the scheduler is ticking:**

   ```bash
   docker compose -f <compose-file> logs --tail 200 superset-worker superset-worker-beat
   ```

   If the logs show `ALERT_REPORTS_NOTIFICATION_DRY_RUN is enabled`, something has
   overridden the shared config's `False` and emails are deliberately not sent.

### How screenshots are taken

The worker opens the chart or dashboard in a headless Chromium at
`WEBDRIVER_BASEURL`, logged in as the report owner.

- **Modes 1 and 2:** `WEBDRIVER_BASEURL = "http://superset:8088"` — the app over the
  compose network. The link in the email is `WEBDRIVER_BASEURL_USER_FRIENDLY`
  (`http://localhost:8088` by default); change it in `superset_config.py` if
  recipients use another address.
- **Mode 3:** Playwright, not Selenium, and `WEBDRIVER_BASEURL` is the public URL.
  The worker therefore needs public DNS and outbound access to
  `SUPERSET_PUBLIC_URL`. The reasons are in "Alerts and reports" in
  [06 Secure mode](06-secure-mode.md).

## Scheduling (users)

1. **Settings → Alerts & Reports**.
2. Pick the **Alerts** or **Reports** tab and click **+ Alert** / **+ Report**.
3. Set the name, the chart or dashboard, the schedule, the recipients and — for an
   alert — the SQL condition and threshold.
4. Save.

**Dashboard tabs.** A dashboard report captures the dashboard's default tab unless
you pick tabs in the report's content settings. Per-tab selection is available
because `ALERT_REPORT_TABS` is on.

Who can create alerts and reports is governed by roles; ask an admin if the menu is
missing.
