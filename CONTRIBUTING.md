# 内容投稿与审校规则

## 教师投稿

教师应通过 Chronovita 教师编辑器完成填写、预览、封存和提交审核。编辑器负责：

- 从课程标题生成可读文件名；
- 从课程与发布身份生成稳定目录；
- 同时导出内容层、格式层、教师稿、预览页及仿真素材；
- 计算归档与文件校验和；
- 自动创建 Pull Request，并返回可点击的审核链接。

教师不需要克隆仓库、创建分支或执行 Git 命令。若发布失败，编辑器应保留已创建的提交或
Pull Request 信息，供安全重试，不得让教师重复上传同一归档。

## 审校人检查

合并前至少确认：

- 自动校验工作流通过；
- 课程标题、正文、关键词、人物及参考资料符合教学要求；
- 预览效果与教师稿一致；
- 仿真任务、人物 persona、事实和知识点没有明显史实错误；
- 变更只新增一个不可变归档，没有覆盖旧版本；
- Pull Request 中不含密钥、个人隐私或未授权素材。

## 发布模式

- `pull_request`：默认模式，适用于教师投稿和普通内容更新。
- `direct_commit`：仅限管理员在界面中二次确认后使用，适用于已审校内容的紧急修正。

无论使用哪种模式，服务端都必须绑定固定仓库 ID、目标根目录和基础分支；客户端不得提交
任意仓库名、分支名或访问令牌。

## 禁止事项

- 不要手工修改 `课程归档清单.json` 或任何哈希字段。
- 不要在已有 `archive_id` 目录内覆盖文件。
- 不要提交草稿、缓存、构建产物、访问令牌、GitHub App 私钥或 `.env`。
- 不要把本仓库当成事务数据库；编辑中的草稿仍由 Chronovita 后端持久化。

维护者可在仓库根目录运行：

```powershell
python -m pip install -r .chronovita/validation-requirements.txt
python -m unittest discover -s tests -v
python scripts/validate_content_archive.py --repository .
```
