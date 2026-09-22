# 非985非211审核规则

`non985-non211.txt` 可复制到目标群的 `review_prompt`。固定资料在前，申请内容由插件追加；只排除985/211，不排除其他双一流高校。学校简称表是辅助识别资料，不是学校白名单；未知学校、歧义和本科院校不明必须保留待确认。

2026-09-22 回顾目标群历史库的 83 条申请（71 种不同答案，含空答案），在实验版基础上补充 HAUST、WIT、QFNU、JXUFE、FJNU、CTGU、SUST、TNU 及中文简称；为 JSU、HNNU 增加歧义说明。只记录学校参考资料，不包含申请人信息。

211排除名单来源：[中国教育在线](https://www.eol.cn/e_html/gk/gxmd/211.shtml)，历史院校名称按现名统一；985高校均包含在该名单内。不能把改名、分校、独立学院仅按字符串包含关系判定。

本轮核对官网标题与域名：

- [河南科技大学](https://www.haust.edu.cn/)、[武汉工程大学](https://www.wit.edu.cn/)、[曲阜师范大学](https://www.qfnu.edu.cn/)
- [江西财经大学](https://www.jxufe.edu.cn/)、[福建师范大学](https://www.fjnu.edu.cn/)、[三峡大学](https://www.ctgu.edu.cn/)
- [吉首大学](https://www.jsu.edu.cn/)、[江苏大学](https://www.ujs.edu.cn/)
- [淮南师范学院](https://www.hnnu.edu.cn/)、[湖南师范大学](https://www.hunnu.edu.cn/)、[天津师范大学](https://www.tjnu.edu.cn/)

陕西科技大学官网本次返回403，SUST采用已有学校信息及历史申请明确写出的“sust（陕西科技大学）”交叉核对；不声称全部映射都获得了本次在线验证。中文简称、英文简称都可能因语境而冲突，提示词中的歧义规则优先。

验证：本轮通过实际安装的 AstrBot OpenAI 适配器、ECNU Plus low 和 JSON Schema 回放 88 条历史/合成样本，88 个审核决定与预期一致，全部返回合法 JSON，无格式重试。另用模拟传输检查实际 SDK 请求体的参数覆盖、关闭思考和普通调用隔离。样本含用于完善提示词的案例，属于回归验证，不代表独立测试集准确率或线上零误判保证。
