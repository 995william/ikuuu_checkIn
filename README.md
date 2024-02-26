# ikuuu每日签到

通用的（机场只要Powered by SSPANEL就可以) 机场签到
## 推送
  该脚本采用的是pushplus的推送方式

# 部署过程
 
1. 右上角Fork此仓库
2. 然后到`Settings`→`Secrets and variables`→`Actions` 新建以下参数：

| 参数    | 是否必须  | 内容                                                                                                     | 
|-------| ------------ |--------------------------------------------------------------------------------------------------------|
| URL   | 是  | 机场地址（例如:https://ikuuu.me）                                                                              |
| INFO  | 是  | 账号密码和sk<br/>```(用<split>分隔) INFO例如: xxx@qq.com<split>密码xxx<split>sk,xxx@qq.com<split>密码xxx<split>sk``` |


sk为pushplus秘钥token
3. 到`Actions`中创建一个workflow，运行一次，以后每天项目都会自动运行。
4. 最后，可以到Run sign查看签到情况，同时也会也会将签到详情推送到pushplus。
