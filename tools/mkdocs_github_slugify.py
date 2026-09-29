"""mkdocs 钩子：让标题锚点与 GitHub 保持一致。

## 为什么需要它

mkdocs 默认用 `toc.slugify` 生成锚点，会把 CJK 标点整段丢掉：
`## 4. 识别层：统一判据` → `#4`（只留序号，不可读）。
而 GitHub / PyCharm / VSCode 预览生成的是完整文本锚点：
`## 4. 识别层：统一判据` → `#4-识别层统一判据`。

两者不一致的后果：文档里手写的目录链接**只能在一种渲染环境里跳转**。
本仓库的文档同时发布到 mkdocs 站点、GitHub 网页，并常被 IDE 直接预览，
所以需要统一到 GitHub 规则（GitHub 规则同时也是多数 IDE 预览的规则）。

## 做法

在解析前给每个 `##` / `###` 标题追加显式锚点属性 `{: #xxx }`
（`attr_list` 扩展已启用），其中 `xxx` 由 `github_slugify()` 按 GitHub 规则算出。
mkdocs 会优先采用显式锚点，因而生成的 id 与 GitHub 完全一致。

注意：`{: #xxx }` 只是**构建期的中间态**，不出现在源码 .md 里——
源码保持干净的纯文本标题，GitHub / IDE 自行按同一规则生成锚点，天然对齐。

## 与 GitHub 规则的一致性

规则（已用 GitHub Markdown API 实测比对，34 个标题零差异）：
1. 转小写
2. 删除除「字母/数字/下划线/汉字/连字符/空格」以外的字符
   （反引号、`：`、`（）`、`.`、`/`、`≠`、`「」` 等一律删除）
3. 空格替换为 `-`

GitHub 实际会再加 `user-content-` 前缀，但页面内片段解析时会剥离，
因此 `#1-它解决什么问题` 这样的链接可直接工作。

## 维护

新增标题无需任何额外操作——钩子自动处理。**但手写目录里的锚点必须与
`github_slugify(标题文本)` 一致**，改标题时记得同步目录。
"""

import re

# GitHub 允许保留的字符：字母、数字、下划线、汉字、连字符、空格
_KEEP = re.compile(r"[^\w\u4e00-\u9fff\- ]+")
# re.M 必需：标题是多行的，缺了它只有第一行会被匹配
_HEADING = re.compile(r"^(#{2,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def github_slugify(text: str) -> str:
    """按 GitHub 规则把标题文本转成锚点 id。"""
    slug = text.strip().lower()
    slug = _KEEP.sub("", slug)
    return slug.replace(" ", "-")


def on_page_markdown(markdown, page, config, files):
    """给所有二~六级标题补上 GitHub 规则的显式锚点。"""

    def replace(match: re.Match) -> str:
        hashes, title = match.group(1), match.group(2)
        # 已手动指定过锚点则不覆盖
        if re.search(r"\{:\s*#[^}]*\}\s*$", title):
            return match.group(0)
        return f"{hashes} {title} {{: #{github_slugify(title)} }}"

    return _HEADING.sub(replace, markdown)
