# YC6 双账号每日抽奖 · 不调用 GPT

适用活动：[yc6.ai 抽奖 1](https://yc6.ai/lottery.html?id=1)。当前网站规则：每天北京时间 **22:00 开奖，每期需重新报名**。

这是为该网站接口编写的双账号版本，替代原压缩包中缺少选择器的通用 Playwright 模板。无需安装浏览器、填写网页选择器、保存网站密码或配置 OpenAI API Key。

## 当前接入状态

最近一次云端运行中，Edge 与 Chrome 两个账号的登录验证和本人结果查询均成功；报名状态返回 `already_joined`，表示当时两账号已报名，因此没有重复提交。离线测试通过。定时配置已写入工作流，但仍需看到 `event=schedule` 的实际运行记录才能确认定时触发。邮件发送还需要配置 SMTP Secrets 并执行一次测试邮件。

接入后由 GitHub Actions 云端运行，电脑关机也不影响云端任务。抽奖、补报、查询及可选通知均为普通 Python，不调用 GPT、不唤醒 Codex / ChatGPT 会话。GitHub Actions 使用 GitHub 自己的运行资源和用量规则，不是 GPT 额度。

## 一次性接入两个已登录账号

只需要配置这两个 Repository Secrets：

| Secret 名称 | 内容 |
|---|---|
| `YC6_TOKEN_EDGE` | Edge 中登录的 yc6.ai 账号的现有登录 token |
| `YC6_TOKEN_CHROME` | Chrome 中登录的另一个 yc6.ai 账号的现有登录 token |

1. 在 **Edge** 的已登录 [yc6.ai 抽奖页面](https://yc6.ai/lottery.html?id=1) 按 **F12**，切到 **Console / 控制台**。执行以下命令，将网站现有登录 token 复制到本机剪贴板：

   ```javascript
   copy(localStorage.getItem('ych_token'))
   ```

   命令只复制当前网站自己的登录状态，不发送到任何其他服务。如果浏览器阻止控制台粘贴，可手动输入这行；不要关闭浏览器安全功能。若复制结果为 `null`，请确认当前地址是 `https://yc6.ai`，且该账号已登录。

2. 打开 [添加 Repository Secret](https://github.com/Ink-Dawn/yc6.ai/settings/secrets/actions/new)，Name 填 `YC6_TOKEN_EDGE`，Secret 粘贴刚才复制的内容并保存。
3. 在 **Chrome** 对另一个账号重复，保存为 `YC6_TOKEN_CHROME`。不要把 token 发给 GPT、写进代码、Issue、普通 Variables 或提交到仓库。
4. 打开 [Actions](https://github.com/Ink-Dawn/yc6.ai/actions/workflows/lottery.yml)，选择 **Run workflow → mode: verify**。两个账号均显示登录验证 `ok` 且没有 `duplicate_account` 后，再运行一次 **mode: join**，验收两个账号的 `joined` / `already_joined` 状态。

两个 token 指向同一个账号时，程序会拒绝将其视作两个账号，并停止这两个重复账号的报名。一个账号失效不会阻止另一个有效账号运行。

## 自动时间（北京时间）

| 时间 | 行为 |
|---|---|
| 09:07 | 两个账号分别报名本期，并检查本人历史中奖记录 |
| 09:37 | 补跑；网站已确认参与的账号跳过报名 |
| 22:07 | 开奖后分别查询本人历史中奖记录 |
| 22:37 | 补查，兼顾延迟开奖或临时网络失败 |

只使用该活动正常的报名接口。不会购买商品、兑换奖品或提交其他账号资料。每次提交后重新读取网站的 `my_entry.joined`，确认报名；报名失败也会继续独立查询中奖结果。

GitHub 定时任务可能延迟或漏调度，不能保证精确到分钟。工作流只在默认分支执行。公开仓库长时间无活动可能禁用定时工作流；程序每 28 天最多更新一次只含时间戳的 `.state/heartbeat.json`，维持正常仓库活动。参考 [GitHub schedule 说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

## 中奖邮件提醒：不调用 GPT

中奖只依据本人登录接口的 `result=won`，不读取公共中奖名单来猜测结果。每次发现一条或多条新的中奖记录时，会通过 QQ 邮箱 SMTP 合并发送一封邮件；同一账号、同一中奖记录会去重，不会在每天查询时重复发送。发送失败时会在后续运行中重试；邮件发送成功后，程序才保存去重状态。邮件只包含账号标签、中奖记录编号和查看记录链接，不会发送卡密。

请打开[仓库 Actions Secrets 设置](https://github.com/Ink-Dawn/yc6.ai/settings/secrets/actions)，在 **New repository secret** 中新增：

| Secret | 内容 |
|---|---|
| `MAIL_USERNAME` | 用于发信的 QQ 邮箱地址 |
| `MAIL_PASSWORD` | QQ 邮箱 SMTP 授权码（不是网页登录密码） |
| `MAIL_TO` | 收件地址 |

使用 QQ 邮箱发信时，需要先在邮箱设置中开启 SMTP/IMAP 服务并生成授权码。若使用你自己的 QQ 邮箱发给自己，`MAIL_USERNAME` 与 `MAIL_TO` 可以相同。不要将授权码发到聊天或写进仓库。

保存后，打开 [Actions 工作流](https://github.com/Ink-Dawn/yc6.ai/actions/workflows/lottery.yml)，点 **Run workflow → mode: test-email**。成功后检查收件箱及垃圾邮件文件夹。该测试邮件仅验证 SMTP 配置，不代表中奖。配置未完成前，中奖记录仍会显示在每次运行的 Actions Summary 中。

本版本不通过 GPT 会话通知，因为那会产生模型用量。网站登录失效、网络错误等也不会调用模型。

## 常见状态

| 状态 | 意义 |
|---|---|
| `missing_token` | 对应 Secret 尚未设置 |
| `authentication_failed` | 网站登录已失效或登录验证失败，需更新对应 Secret |
| `duplicate_account` | 两个 token 实际属于同一账号 |
| `joined` | 网站已确认本期报名 |
| `already_joined` | 本期已报名，未重复提交 |
| `not_open` | 活动下架、当期截止或尚未开放 |
| `participation_failed` | 本次报名未能确认；仍会尝试查结果 |
| `result_check_failed` | 本次本人结果查询失败，下次继续 |
| `channel_not_configured` | 检测到中奖，但邮件 Secrets 未配置 |
| `already_notified` | 这些中奖记录此前已成功通知 |
| `delivery_failed` | 邮件发送失败；后续定时运行会重试 |
| `sent_state_unrecorded` | 邮件已提交，但去重状态保存失败；后续可能重复发送 |

网站可以使现有 token 过期或撤销，无法承诺永久免维护。失效后只需更新对应 Secret；日常无需登录点击。

Secrets 的正确设置方法见 [GitHub 官方文档](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)。本仓库只保存程序、测试和最少运行状态，永不保存网站凭据。

## 暂停与测试

暂停：在 Actions 中选择 `YC6 two-account lottery (no GPT)` → `…` → `Disable workflow`。通知去重文件保留不动。

本地离线测试（不访问网站、不消耗抽奖机会）：

```shell
python -m unittest discover -s tests -v
```

`Offline tests` 工作流会自动验证提交和 PR，无需任何账号 Secrets。手动 `verify` 只验证登录，不报名、不发通知；`join` 会真实报名，`check` 仅查结果。
