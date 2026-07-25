# 内容投稿与审校规则

## 教师投稿

教师应通过 Chronovita 教师编辑器完成填写、预览、校验、封存和提交审核。编辑器负责：

- 按课程、人物、关键词或关卡标题生成可读文件名；
- 按封存身份和版本生成稳定目录；
- 对课程导出内容层、格式层、教师稿、预览页和仿真素材；
- 对人物、关键词和关卡导出其精确封存 JSON；
- 计算清单、源契约和每个文件的 SHA-256；
- 自动创建 Pull Request，并返回可点击的审核链接。

教师不需要克隆仓库、创建分支或执行 Git 命令。发布失败时，编辑器应保留任务、提交或 Pull Request 信息供安全重试，不能重复上传同一归档。

## 审校人检查

合并前至少确认：

- 自动归档校验工作流通过；
- 标题、正文、关键词、人物和参考资料符合教学要求；
- 人物 persona、事实边界和关卡规则没有明显史实错误；
- 资产种类、名称、版本和来源与提交说明一致；
- 变更只新增不可变归档，没有覆盖旧版本；
- Pull Request 不含密钥、个人隐私或未授权素材。

## 发布模式

- `pull_request`：默认模式，适用于教师投稿和普通内容更新。
- `direct_commit`：只供管理员在界面中二次确认后使用，适用于已经审校的紧急修正。

两种模式都必须绑定固定仓库 ID、目标根目录和基础分支。客户端不得提交任意仓库名、分支名、目录前缀或访问令牌。

## 归档目录

- 课程：`courses/<course_id>/releases/<release_id>/<archive_id>/`
- 人物：`assets/people/<asset_id>/versions/vNNN/<archive_id>/`
- 关键词：`assets/keywords/<asset_id>/versions/vNNN/<archive_id>/`
- 关卡：`assets/scenarios/<asset_id>/versions/vNNN/<archive_id>/`

请勿手工修改归档清单或任何哈希字段，也不要在已有 `archive_id` 目录内覆盖文件。草稿、缓存、构建产物、访问令牌、GitHub App 私钥和 `.env` 均不得提交。

## 维护者验证

在仓库根目录运行：

```powershell
python -m pip install -r .chronovita/validation-requirements.txt
python -m unittest discover -s tests -v
python scripts/validate_content_archive.py --repository .
```
