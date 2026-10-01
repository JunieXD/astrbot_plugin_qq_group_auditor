# 2026-10-01 四非简称修正

本次只更新四非群的审核提示词及回归样本，运行代码仍为 v0.4.1。

## 规则调整

- 增加 CQUPT=重庆邮电大学、HAUT=河南工业大学、NUC=中北大学，修正真实申请中被模型忽略的常见缩写。
- 增加 NEAU=东北农业大学，涵盖带较长附加说明的申请。
- 北京交通大学增加旧称“北方交通大学 / Northern Jiaotong University”参考。
- NJTU 不直接推断为南京工业大学；仅写 NJTU 或与全称冲突时留人工。明确的南京工业大学与 NJTECH 按学校身份正常审核。
- 保留简称自动审核；冲突、未知信息仍按群 ignore 策略留人工。固定名单和规则仍在动态申请信息之前，名单范围未变。

官网核对：
- https://www.cqupt.edu.cn/
- https://www.haut.edu.cn/
- https://www.nuc.edu.cn/
- https://en.bjtu.edu.cn/AboutBJTU_20161201183113092917/index.htm
- https://en.njtech.edu.cn/

## 验证

使用 ECNU Plus、enabled/low、JSON Schema，对 `tests/fixtures/school_alias_regression.json` 中28个样本各测2轮，单并发，不自动网络重试，不调用 QQ 审批。

56次调用均返回有效 JSON，56次通过/不通过结果符合预期。新增的 NJTU 歧义、全称冲突、常见简称、旧校名及正常南京工业大学申请均达到预期；原有风险样本未出现误放。

仍有一次理由不够准确：第二轮 `24-HEBUT` 未识别到河北工业大学，返回“无法确认为已知高校…待人工确认”，最终没有通过。保留这一结果，不将56次资格结果符合预期称为56次校名理解完全正确。样本针对已知问题，不代表独立生产准确率或绝对不会误判。

提示词/README 相关17项自动化测试通过，git diff --check 通过。
