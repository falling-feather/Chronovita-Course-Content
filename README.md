# Chronovita 课程内容历史库

本仓库是 Chronovita 教师编辑器生成内容的**私有、可审校、可追溯归档库**。它保存课程、人物档案、关键词档案和关卡规则的不可变版本及 Git 历史，不替代运行时数据库，也不要求教师学习 Git。

## 教师如何使用

1. 在 Chronovita 教师编辑器中选择“课程内容”“人物档案”“关键词档案”或“关卡规则”。
2. 填写、预览并封存；封存后形成带版本号和校验和的内容快照。
3. 点击“提交审核”；编辑器自动命名文件、打包归档并创建 Pull Request。
4. 审校人查看内容和自动校验结果，确认后合并。

默认方式是“提交审核”。“直接发布”只供管理员在二次确认后使用。普通教师无需克隆仓库、创建分支、执行 Git 命令，也不会在编辑界面看到访问令牌。

## 仓库结构

```text
.
├─ .chronovita/
│  ├─ repository-policy.json
│  └─ schemas/archive/v1/
├─ .github/
│  ├─ workflows/validate-content.yml
│  └─ pull_request_template.md
├─ courses/
│  └─ <course_id>/releases/<release_id>/<archive_id>/
├─ assets/
│  ├─ people/<asset_id>/versions/vNNN/<archive_id>/
│  ├─ keywords/<asset_id>/versions/vNNN/<archive_id>/
│  └─ scenarios/<asset_id>/versions/vNNN/<archive_id>/
├─ scripts/
│  └─ validate_content_archive.py
└─ CONTRIBUTING.md
```

课程归档必须包含 `课程归档清单.json`；内容资产归档必须包含 `内容资产归档清单.json`。清单记录稳定身份、源契约、全部文件路径、字节数和 SHA-256。任何修改都必须生成新的发布版本或资产版本，不能覆盖已有 `archive_id`。

`tests/fixtures/` 中的课程和人物内容仅用于自动化契约测试，不属于教师正式课程资料。

## 安全边界

- 仓库保持私有，GitHub 仓库数字 ID 为 `1311692460`。
- 访问令牌只由 Chronovita 后端安全配置提供，不得提交 token、私钥、`.env` 或类似凭据。
- 自动校验会拒绝重复 JSON 键、符号链接、越界路径、未登记文件、校验和篡改和凭据类字段。
- `main` 只接收完整、可验证的封存归档，不接收编辑中的草稿。

课程归档契约和内容资产契约均来自
[`falling-feather/Chronovita@class`](https://github.com/falling-feather/Chronovita/tree/class)。
课程契约基线为 `5e9bedcbb3181606c618d431b2df9e77a8c25905`，内容资产契约基线为 `909a868cfe1a1b447ddb0d967c548a9993e62c79`。具体投稿和维护规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。
