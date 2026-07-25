# Chronovita 课程内容历史库

本仓库是 Chronovita 教师编辑器生成内容的**私有、可审校、可追溯归档库**。
它保存已经封存的课程快照和 Git 历史，不替代 Chronovita 运行时数据库，也不要求教师学习 Git。

## 教师如何使用

1. 在 Chronovita 教师编辑器中填写或修改课程。
2. 预览并封存，系统生成可复现的课程归档。
3. 点击“提交审核”，系统自动命名文件并创建 Pull Request。
4. 审校人确认后合并；平台随后可以按归档身份读取该版本。

默认发布方式是 Pull Request。管理员可在明确确认后使用直接提交，但教师界面不会把 Git
分支、提交命令或访问令牌暴露给普通使用者。

## 仓库结构

```text
.
├── .chronovita/
│   ├── repository-policy.json
│   └── schemas/archive/v1/
├── .github/
│   ├── workflows/validate-content.yml
│   └── pull_request_template.md
├── courses/
│   └── <course_id>/releases/<release_id>/<archive_id>/
├── scripts/
│   └── validate_content_archive.py
└── CONTRIBUTING.md
```

每个归档目录必须含有 `课程归档清单.json`。清单记录课程、发布、渲染器、全部文件路径、
字节数和 SHA-256；同一份归档一旦进入 `main`，不得原位覆盖，任何修改都必须产生新的
`release_id` 或 `archive_id`。

## 安全边界

- 仓库保持私有，当前 GitHub 仓库数字 ID 为 `1311692460`。
- 密钥只由 Chronovita 后端的安全配置提供，**不得提交 token、私钥或 `.env` 文件**。
- 投稿默认进入短生命周期分支，由自动校验和人工审校共同把关。
- `main` 仅保存完整、可验证的封存归档，不接收未封存草稿。

契约源自
[`falling-feather/Chronovita@class`](https://github.com/falling-feather/Chronovita/tree/class)
的 `course-archive/v1`，冻结基线为提交 `5e9bedcbb3181606c618d431b2df9e77a8c25905`。
具体投稿和维护规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。
