---
name: ielts-vocab
description: 雅思背单词训练系统。This skill should be used when the user wants to study IELTS vocabulary, do daily vocab drills, review due words, check study stats, or add new words ("背单词/背雅思单词/今日任务/复习/测验/学了多少/加个词"). It manages spaced-repetition scheduling (SM-2) across sessions via files, renders interactive HTML flashcards with paraphrase drills, and reports streaks and mastery progress.
agent_created: true
---
# IELTS Vocab — 雅思背单词训练系统

## Overview

带 SM-2 间隔重复调度的雅思词汇学习系统。四份分类词库（听力场景/写作学术/口语地道/阅读学术，共 200 词）+ 自定义词库，学习状态持久化在 `user_data.json`，跨会话追踪每个词的记忆周期。

## 快速开始

```bash
PY=<python 解释器绝对路径>
S=<skill 目录>/scripts/session.py

# 1. 今日任务（到期复习 + 新词）
"$PY" -X utf8 "$S" plan            # JSON 输出：due_reviews + new_words
"$PY" -X utf8 "$S" plan --csv     # 仅 id 列表

# 2. 取词详情（喂给卡片渲染）
"$PY" -X utf8 "$S" words --ids "habitat,deposit" --lang all

# 3. 记录成绩（用户完成卡片自评后）
"$PY" -X utf8 "$S" record --results "habitat=good,deposit=again"

# 4. 统计
"$PY" -X utf8 "$S" stats

# 5. 每日负载配置（2026-08-31 用户定稿：30 新词/日，防过载开启）
"$PY" -X utf8 "$S" config                        # 查看
"$PY" -X utf8 "$S" config --new 30 --due 90      # 修改并持久化到 settings.json
"$PY" -X utf8 "$S" config --auto-reduce 1 --threshold 50   # 复习积压>50 时新词按比例缩减
"$PY" -X utf8 "$S" config --reset                # 恢复默认

# 6. 本地桥接服务（卡片页自动保存用；先 ping，不通再后台启动）
"$PY" -X utf8 "$S" ping                                   # 不通时:
"$PY" -X utf8 "$S" serve --port 8765                      # 后台运行，常驻
```

**必须加 `-X utf8`**：Windows 上中文参数/输出会因默认 GBK 编码乱码或崩溃。

## 每次会话的标准流程

```
用户: "背单词" / "今日任务"
  ↓
1. session.py plan          → 拿到今日词单（到期复习在前、新词在后）
2. session.py words --ids … → 取词条完整数据
3. 检查桥接服务：session.py ping → 不通则后台启动 serve --port 8765
   （卡片页"保存学习记录"要 POST http://127.0.0.1:8765/record 直接入库）
4. 首选：生成 HTML 页（card_template.html 替换 {{WORDS_JSON}}）→ present_files 展示。
   present_files 预览面板是真实 http://127.0.0.1 源，fetch 本地桥接畅通（已实测自动保存成功）。
   组件沙箱（show_widget）对 127.0.0.1 完全网络隔离，不要用组件做保存入口。
   **用户明确要求（2026-08-31）：只在预览页打开，不要自动弹外部浏览器窗口。**
   仅当用户主动说"用浏览器打开/在浏览器里背"时才 Start-Process；组件仅用于无保存需求的展示。
   - 回忆模式：先只显示单词，点"显示释义"后展开 cn/例句/同义替换
   - 紧凑字体（用户要求不显眼）：单词 17px/500，正文 13px，辅助 11-12px；
     按钮高约 26-30px；卡片左对齐，max-width 560px
   - 按钮一律透明底 + 0.5px 细边框（用户不要彩色按钮）：
     background:transparent;border:0.5px solid var(--color-border-secondary);
     border-radius:var(--border-radius-md)
   - 按钮文字颜色必须 color:inherit 跟随软件主题（黑背景→白字，浅色→黑字，
     与单词同色），严禁写死 #000/#fff 等颜色；textarea 同理 background:transparent + color:inherit
   - 保存按钮 fetch("http://127.0.0.1:8765/record") 入库（端口依次试 8765/8766/8767）。
     必须用 Content-Type: text/plain 简单请求（application/json 会触发 CORS 预检）；
     服务端已处理 OPTIONS 预检。保存失败回退剪贴板：结果串粘贴回对话，Agent 执行 record
   - 组件不要加 sr-only 隐藏标题（该环境会显示出来），卡片直接从 #app 容器开始
   - 样式用 CSS 变量（--color-*）自适应明暗主题，勿写死颜色
5. 备选：用户要完整浏览器页或组件环境不可用时，
   生成 HTML 页（card_template.html，同样走桥接直传 + 剪贴板回退）
6. Agent 收到"请记录学习结果：id=grade,…"后执行 session.py record --results …（不要只回复文字）
6. 汇报本轮统计（正���率、明日到期数）
```

`plan` 退出码为 1 表示今天没有任务（新词学完且无到期），此时直接告知用户即可，不要报错。

## 命令细节

### plan
- `--list listening|writing|speaking|reading|custom` 按库过滤
- 默认值来自 `settings.json`（`config` 命令写入）；`--new N` / `--due N` 可临时覆盖单次
- **防过载**（auto_reduce=true 时）：到期复习积压 > threshold（默认 50）→ 新词按
  `threshold/backlog` 比例缩减，下限 5；输出 `adjusted` 字段说明缩减原因，
  `backlog` 为完整积压数——汇报时若见 `adjusted`，提醒用户"今天复习较多，新词自动减少"
- `--due` 上限只截断显示，不丢数据：被截的词仍在库中，明天照常到期
- 同一天多次 plan 不会重复发新词（按 history 当日 new 计数扣减）
- 输出 id 列表带调度原因：到期复习优先于新词

### words
- `--lang en` 只显示英文侧（做"看词想义"）；`--lang cn` 只显示中文侧；`--lang all` 全字段
- 生成卡片前用 `all` 拿全量字段

### record
- grade 取值：`again`(忘了) / `hard`(困难) / `good`(记得) / `easy`(轻松)
- SM-2 规则：again → 当天重排+EF 降；good → rep+1, interval 1→6→×EF
- 输出每词的新 EF / 间隔 / 到期日；`leech: true` 表示错 ≥8 次，建议重点讲解

### stats
- 覆盖率 = seen / 200、掌握数（rep≥2 且 interval≥7）、今日到期、连击天数、leech 词

### add
- 交互中用户说"把 XX 加进词库"时使用；id=word 小写，若跨库重复返回 error
- 生成 `wordlist_custom.json`（首次自动创建）

### config
- 查看/持久化每日负载设置到 `settings.json`，作为 `plan` 的默认值
- 当前用户配置（2026-08-31 定稿）：new_per_day=30, max_due_per_day=90, auto_reduce=true, reduce_threshold=50
- 用户嫌节奏慢/太快时，先建议改 config 而不是每次手动传 `--new`

## 文件布局

```
ielts-vocab/
├── SKILL.md                     # 本文件
├── scripts/
│   ├── scheduler.py             # SM-2 算法（纯函数，可单测）
│   └── session.py               # 会话管理 CLI
├── assets/
│   ├── card_template.html       # 交互卡片模板（替换 {{WORDS_JSON}}）
│   └── data/
│       ├── wordlist_listening.json   # 61 词，Section 1-4 场景
│       ├── wordlist_writing.json     # 50 词，Task 1/2 学术
│       ├── wordlist_speaking.json    # 42 词，地道表达/搭配
│       ├── wordlist_reading.json     # 47 词，学术阅读
│       ├── wordlist_custom.json      # 用户自建（运行时生成）
│       ├── user_data.json            # 学习状态（运行时生成，勿手编）
│       └── settings.json             # 每日负载配置（config 命令写入）
```

## 交互卡片说明

- 载体优先级（用户定稿）：**present_files 预览页**（真实 http 源，桥接直存，默认方式）
  > 外部浏览器（仅用户主动要求时）> show_widget 组件（网络隔离，仅展示用）。
  保存按钮统一走本地桥接 POST http://127.0.0.1:8765/record 自动入库；不通时回退剪贴板。
  不要主动 Start-Process 弹浏览器窗口
- 逐词展示：word + 音标 + 词性 + 释义 + 双语例句 + **同义替换**（雅思核心考点）
- 点击单词朗读（SpeechSynthesis，en-GB）
- 四键自评 → 生成 `id=grade` 串 → 回传
- Agent 收到"请记录学习结果："开头的消息时，必须执行 record 命令，不要只回复文字

## 行为准则

- 学习数据是事实记录，**不猜测**成绩：以卡片页回传的自评串为准
- `user_data.json` 只通过 session.py 写入；调参/排查时可读，不手改
- 用户问"学到哪了/多少词了"→ `stats`；问"明天复习什么"→ `plan`
- 主动提示：当 stats 显示 leech 词（≥8 次答错）时，建议用户用"联想记忆/词根词缀"专项突破
- 生成卡片页时文件名带日期（vocab_YYYYMMDD.html），避免覆盖历史

## 跨设备同步（Git 私有仓库，2026-08-31 启用）

- 远端：`git@github.com:enzo-H/-ielts-vocab.git`（main 分支，skill 目录即仓库工作目录）
- 用户说"**同步进度**"时执行：
  - 学完 push：`git add . && git commit -m "进度 YYYY-MM-DD" && git push`
  - 换机开始前 pull：`git pull --rebase`
- 约定：同一时段只用一台电脑学；beacon_log/bridge_port 已在 .gitignore 排除
- 新机器接入：`cd ~/.workbuddy/skills/ && git clone git@github.com:enzo-H/-ielts-vocab.git ielts-vocab`
  （需先配好该机的 SSH 公钥；Python 用该机 WorkBuddy 托管版本）
- push 被拒 → 先 `git pull --rebase`；冲突 → 保留数据更全的 user_data.json
