# -*- coding: utf-8 -*-
"""
IKUUU 机场多账号自动签到脚本
特性：
1. 自动获取与探测 IKUUU 官方最新可用域名
2. Playwright 模拟浏览器自动化登录，绕过前端验证风控
3. 自动签到并查询当前账号剩余流量、已用流量和剩余天数
4. 支持多账号批量签到，邮箱自动脱敏处理
5. 支持企业微信自建应用（合并配置 WECHAT_WORK）、群机器人 Webhook 以及 PushPlus 推送
6. 结构化精美消息模版通知
"""

from playwright.sync_api import sync_playwright
import requests
import os
import sys
import time
import re
from datetime import datetime

# 保证标准输出支持 UTF-8（防止 Windows 控制台因输出特殊字符抛出 gbk 编码错误）
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)


# ─────────────────────────────────────────────
# 推送通知模块（原生实现，无需外部第三方依赖）
# ─────────────────────────────────────────────
def get_wechat_config():
    """
    获取企业微信应用配置，支持单变量合并配置与旧独立变量向下兼容
    - 合并变量：WECHAT_WORK 或 WX_CONFIG，格式为：corpid,corpsecret,agentid
      支持英文逗号、分号或冒号分隔
    - 兼容回退：WX_CORPID、WX_CORPSECRET、WX_AGENTID
    """
    raw = os.environ.get('WECHAT_WORK') or os.environ.get('WX_CONFIG')
    if raw:
        # 支持逗号、分号或冒号作为分隔符
        parts = [p.strip() for p in re.split(r'[,;:]', raw) if p.strip()]
        if len(parts) >= 3:
            return parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            return parts[0], parts[1], ''

    # 回退到旧有的独立环境变量
    corpid = (os.environ.get('WX_CORPID') or '').strip()
    corpsecret = (os.environ.get('WX_CORPSECRET') or '').strip()
    agentid = (os.environ.get('WX_AGENTID') or '').strip()
    return corpid, corpsecret, agentid


def send_wechat_app(msg, corpid, corpsecret, agentid, touser='@all'):
    """企业微信自建应用通知"""
    if not (corpid and corpsecret and agentid):
        return False
    try:
        token_url = f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}&corpsecret={corpsecret}'
        token_resp = requests.get(token_url, timeout=10).json()
        access_token = token_resp.get('access_token')
        if not access_token:
            print(f"[企业微信应用通知失败] 获取 access_token 失败: {token_resp}")
            return False

        send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
        data = {
            "touser": touser,
            "msgtype": "text",
            "agentid": agentid,
            "text": {"content": msg},
            "safe": 0,
        }
        resp = requests.post(send_url, json=data, timeout=10).json()
        print(f"[企业微信应用通知] {resp}")
        return resp.get('errcode') == 0
    except Exception as e:
        print(f"[企业微信应用通知异常] {e}")
        return False


def send_wechat_webhook(msg, webhook_url):
    """企业微信群机器人 Webhook 通知"""
    if not webhook_url:
        return False
    try:
        resp = requests.post(
            webhook_url.strip(),
            json={"msgtype": "text", "text": {"content": msg}},
            timeout=10,
        ).json()
        print(f"[企业微信群机器人通知] {resp}")
        return resp.get('errcode') == 0
    except Exception as e:
        print(f"[企业微信群机器人通知异常] {e}")
        return False


def send_pushplus(title, content, token):
    """PushPlus 微信推送通道"""
    if not token:
        return False
    try:
        url = 'http://www.pushplus.plus/send'
        data = {
            'token': token.strip(),
            'title': title,
            'content': content,
            'template': 'markdown'
        }
        resp = requests.post(url, json=data, timeout=15).json()
        print(f"[PushPlus 通知] {resp}")
        return resp.get('code') == 200
    except Exception as e:
        print(f"[PushPlus 通知异常] {e}")
        return False


def dispatch_notifications(title, message):
    """统一派发所有已配置渠道的通知"""
    # 1. 企业微信应用推送
    corpid, corpsecret, agentid = get_wechat_config()
    if corpid and corpsecret and agentid:
        print("\n[消息推送] 正在发送企业微信应用通知...")
        send_wechat_app(message, corpid, corpsecret, agentid)

    # 2. 企业微信群机器人推送
    webhook_url = os.environ.get('WX_WEBHOOK') or ''
    if webhook_url:
        print("[消息推送] 正在发送企业微信机器人通知...")
        send_wechat_webhook(message, webhook_url)

    # 3. PushPlus 推送
    pushplus_token = os.environ.get('PUSHPLUS_TOKEN') or os.environ.get('PUSH_PLUS_TOKEN') or ''
    if pushplus_token:
        print("[消息推送] 正在发送 PushPlus 通知...")
        send_pushplus(title, message, pushplus_token)


# ─────────────────────────────────────────────
# 邮箱脱敏工具函数
# ─────────────────────────────────────────────
def mask_email(email):
    """abc12345@gmail.com -> a******5@gmail.com"""
    if '@' not in email:
        return email

    name, domain = email.split('@', 1)
    if len(name) <= 1:
        masked = name
    elif len(name) == 2:
        masked = name[0] + '*'
    else:
        masked = name[0] + '*' * (len(name) - 2) + name[-1]

    return f'{masked}@{domain}'


# ─────────────────────────────────────────────
# 自动获取与探测最新可用域名
# ─────────────────────────────────────────────
LANDING_PAGES = [
    'https://ikuuu.win'
]

FALLBACK_DOMAINS = [
    'https://ikuuu.top',
    'https://ikuuu.pw',
    'https://ikuuu.org',
    'https://ikuuu.one',
    'https://ikuuu.dev',
    'https://ikuuu.co'
]


def is_valid_login_domain(domain):
    """检测域名是否为可用且未失效的真实登录地址"""
    if not domain:
        return False
    login_url = f"{domain.rstrip('/')}/auth/login"
    try:
        resp = requests.get(
            login_url,
            headers={'User-Agent': USER_AGENT},
            timeout=8,
            allow_redirects=True
        )
        if resp.status_code == 200:
            text = resp.text
            # 必须排除导航发布页，且确认含有实际系统的登录标识
            if ('originBody' in text or 'login' in text.lower()) and '最新域名' not in text:
                return True
    except Exception:
        pass
    return False


def extract_domains_from_landing_page(landing_url):
    """使用 Playwright 访问官方发布页，动态解析最新域名列表"""
    domains = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()
            page.goto(landing_url, wait_until='networkidle', timeout=15000)

            try:
                page.wait_for_selector('#domain-list .domain-card', timeout=5000)
            except Exception:
                pass

            links = page.eval_on_selector_all(
                'a[href]',
                'elements => elements.map(e => e.href)'
            )
            for link in links:
                m = re.search(r'https?://(ikuuu\.[a-z0-9]+)', link)
                if m:
                    d = f"https://{m.group(1)}"
                    if d != landing_url and d not in domains:
                        domains.append(d)
            browser.close()
    except Exception as e:
        print(f"[发布页解析异常] {landing_url} -> {e}")
    return domains


def get_target_domain():
    """获取当前可用的 IKUUU 主域名（环境变量优先 -> 发布页提取 -> 备用池探测）"""
    # 1. 自定义域名优先
    custom_domain = os.environ.get('IKUUU_DOMAIN')
    if custom_domain:
        custom_domain = custom_domain.strip().rstrip('/')
        print(f"[域名解析] 检测到自定义域名环境变量: {custom_domain}")
        if is_valid_login_domain(custom_domain):
            print(f"[域名解析] 自定义域名可用性验证通过: {custom_domain}")
            return custom_domain
        else:
            print("[域名解析] 自定义域名无法访问或已失效，转入自动探测...")

    candidates = []

    # 2. 从官方发布页动态爬取
    for landing_page in LANDING_PAGES:
        print(f"[域名解析] 正在访问官方发布页获取最新域名: {landing_page}")
        extracted = extract_domains_from_landing_page(landing_page)
        if extracted:
            print(f"[域名解析] 从发布页成功提取到域名: {extracted}")
            candidates.extend(extracted)
            break

    # 3. 合并备用池并去重
    candidates.extend(FALLBACK_DOMAINS)
    seen = set()
    unique_candidates = [d for d in candidates if not (d in seen or seen.add(d))]
    print(f"[域名解析] 待探测候选域名列表: {unique_candidates}")

    # 4. 逐一健康探测
    for domain in unique_candidates:
        print(f"[域名解析] 正在探测域名可用性: {domain} ...")
        if is_valid_login_domain(domain):
            print(f"[域名解析] 成功选定可用主域名: {domain}")
            return domain

    # 5. 保底返回
    fallback = 'https://ikuuu.top'
    print(f"[域名解析] 所有探测未响应，使用保底域名: {fallback}")
    return fallback


# ─────────────────────────────────────────────
# 账户剩余流量与剩余天数解析
# ─────────────────────────────────────────────
def get_user_account_info(session, base_url):
    """
    通过已登录的 session 请求用户中心页面，解析剩余流量、已用流量与剩余有效期天数
    返回格式字典:
    {
        'traffic_remain': '...',
        'traffic_used': '...',
        'traffic_total': '...',
        'expire_status': '...',
        'expire_days': int or None
    }
    """
    info = {
        'traffic_remain': '未知',
        'traffic_used': '',
        'traffic_total': '',
        'expire_status': '未知',
        'expire_days': None
    }
    user_url = f"{base_url.rstrip('/')}/user"

    try:
        resp = session.get(
            user_url,
            headers={
                'referer': f"{base_url}/auth/login",
                'user-agent': USER_AGENT
            },
            timeout=15
        )
        if resp.status_code != 200:
            return info

        html = resp.text

        # 1. 提取剩余流量
        remain_patterns = [
            r'(?:剩余流量|剩余可用|剩余|未使用)[^\d<]*[:：\s]*([0-9.]+\s*(?:KB|MB|GB|TB|B))',
            r'([0-9.]+\s*(?:KB|MB|GB|TB))\s*(?:剩余|可用)',
            r'id=[\'"](?:unusedTraffic|remainingTraffic)[\'"][^>]*>([^<]+)<',
            r'class=[\'"][^\'"]*traffic-remain[^\'"]*[\'"][^>]*>([^<]+)<',
            r'var\s+unusedTraffic\s*=\s*[\'"]([^\'"]+)[\'"]',
            r'[\'"]unused_traffic[\'"]\s*:\s*[\'"]?([0-9.]+\s*[KMGT]?B)[\'"]?'
        ]
        for pat in remain_patterns:
            m = re.search(pat, html, re.I)
            if m:
                info['traffic_remain'] = m.group(1).strip()
                break

        # 辅助提取已用流量与总流量
        m_used = re.search(r'(?:今日已用|已用流量|已用)[^\d<]*[:：\s]*([0-9.]+\s*(?:KB|MB|GB|TB|B))', html, re.I)
        if m_used:
            info['traffic_used'] = m_used.group(1).strip()

        m_total = re.search(r'(?:总计|总流量|总计流量)[^\d<]*[:：\s]*([0-9.]+\s*(?:KB|MB|GB|TB|B))', html, re.I)
        if m_total:
            info['traffic_total'] = m_total.group(1).strip()

        # 2. 提取等级/账号到期时间与天数
        if re.search(r'(?:永久有效|无限期|长期有效)', html):
            info['expire_status'] = '永久有效'
        else:
            expire_patterns = [
                r'(?:等级过期时间|账户到期时间|会员到期时间|到期时间|服务到期)[^\d<]*[:：\s]*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:\s+[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?)',
                r'class=[\'"][^\'"]*expire[^\'"]*[\'"][^>]*>([0-9]{4}-[0-9]{2}-[0-9]{2}[^<]*)<',
                r'var\s+class_expire\s*=\s*[\'"]([0-9]{4}-[0-9]{2}-[0-9]{2}[^\'"]*)[\'"]',
                r'[\'"]class_expire[\'"]\s*:\s*[\'"]([0-9]{4}-[0-9]{2}-[0-9]{2}[^\'"]*)[\'"]'
            ]
            for pat in expire_patterns:
                m = re.search(pat, html, re.I)
                if m:
                    expire_str = m.group(1).strip()
                    try:
                        date_part = expire_str.split()[0]
                        exp_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                        now_date = datetime.now().date()
                        diff_days = (exp_date - now_date).days
                        info['expire_days'] = diff_days
                        if diff_days >= 0:
                            info['expire_status'] = f"剩余 {diff_days} 天 ({date_part})"
                        else:
                            info['expire_status'] = f"已过期 {abs(diff_days)} 天 ({date_part})"
                    except Exception:
                        info['expire_status'] = expire_str
                    break

            if info['expire_status'] == '未知':
                m_days = re.search(r'(?:距离到期还有|剩余)\s*(\d+)\s*天', html)
                if m_days:
                    days = int(m_days.group(1))
                    info['expire_days'] = days
                    info['expire_status'] = f"剩余 {days} 天"

    except Exception as e:
        print(f"[获取账号数据异常] {e}")

    return info


# ─────────────────────────────────────────────
# Playwright 登录获取 Cookie
# ─────────────────────────────────────────────
def playwright_login(email, passwd, base_url):
    safe_email = mask_email(email)
    print(f'\n启动浏览器进行登录：{safe_email}')

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled']
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN"
        )

        # 隐藏自动化 webdriver 特征
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        page = context.new_page()

        # 打开登录页
        login_url = f'{base_url}/auth/login'
        print(f'正在打开登录页: {login_url}')
        page.goto(login_url, wait_until='networkidle')

        print('填写账号密码...')
        page.fill('#email', email)
        page.fill('#password', passwd)

        print('点击验证按钮...')
        try:
            page.click('.geetest_btn_click', timeout=5000)
            print('验证按钮点击成功')
        except Exception:
            print('未找到验证按钮，继续登录')

        time.sleep(2)
        print('点击登录按钮...')
        page.click('button[type="submit"]')

        # 等待页面重定向跳转
        time.sleep(5)
        print('获取登录 Cookie...')
        cookies = context.cookies()
        browser.close()
        return cookies


# ─────────────────────────────────────────────
# 单账号签到与信息汇总
# ─────────────────────────────────────────────
def checkin_one_account(email, passwd, base_url):
    """
    单账号登录、签到并查询流量与天数
    返回数据字典包含：email, success, msg, traffic, expire
    """
    safe_email = mask_email(email)
    check_url = f'{base_url}/user/checkin'

    header = {
        'origin': base_url,
        'referer': f'{base_url}/user',
        'user-agent': USER_AGENT
    }

    record = {
        'safe_email': safe_email,
        'success': False,
        'status_text': '未知',
        'traffic_remain': '未知',
        'traffic_detail': '',
        'expire_status': '未知'
    }

    try:
        # 1. Playwright 登录
        pw_cookies = playwright_login(email, passwd, base_url)
        if not pw_cookies:
            raise Exception('未获取到 Cookie，可能登录失败或触发图形验证')

        session = requests.session()
        for c in pw_cookies:
            name = c.get('name')
            value = c.get('value')
            if name and value:
                session.cookies.set(name, value)

        # 2. 执行签到
        print('开始执行签到请求...')
        resp = session.post(url=check_url, headers=header, timeout=20)
        try:
            result = resp.json()
            msg = result.get('msg', '未知结果')
            ret = result.get('ret', 0)
            print(f"签到响应: {result}")
        except Exception:
            msg = resp.text[:100] if resp.text else '无响应内容'
            ret = 0

        # 判断签到状态
        if ret == 1 or '获得' in msg:
            record['success'] = True
            record['status_text'] = f"✅ 签到成功 ({msg})"
        elif '已经签到' in msg or '已签到' in msg:
            record['success'] = True
            record['status_text'] = f"ℹ️ {msg}"
        else:
            record['success'] = False
            record['status_text'] = f"⚠️ {msg}"

        # 3. 查询当前账号剩余流量与剩余天数
        print('正在查询当前账号流量与有效期...')
        user_info = get_user_account_info(session, base_url)
        record['traffic_remain'] = user_info['traffic_remain']
        if user_info['traffic_used'] or user_info['traffic_total']:
            record['traffic_detail'] = f" (已用: {user_info['traffic_used'] or '未知'} / 总计: {user_info['traffic_total'] or '未知'})"
        record['expire_status'] = user_info['expire_status']

        print(f"账号 {safe_email} 状态: {record['status_text']} | 剩余流量: {record['traffic_remain']}{record['traffic_detail']} | 到期: {record['expire_status']}")

    except Exception as e:
        record['success'] = False
        record['status_text'] = f"❌ 失败: {str(e)}"
        print(f"账号 {safe_email} 处理异常: {e}")

    return record


# ─────────────────────────────────────────────
# 重新设计的消息模版构建函数
# ─────────────────────────────────────────────
def build_notification_message(records, base_url):
    """
    根据所有账号的签到及资产数据，构建层次分明、排版美观的消息卡片
    """
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total_count = len(records)
    success_count = sum(1 for r in records if r['success'])
    fail_count = total_count - success_count

    lines = [
        "【IKUUU 机场签到通知】",
        "═" * 32,
        f"⏰ 签到时间: {now_str}",
        f"📊 运行汇总: 共 {total_count} 个账号 | ✅ 成功 {success_count} | ❌ 失败 {fail_count}",
        f"🌐 接入节点: {base_url}",
        "─" * 32,
    ]

    for idx, r in enumerate(records, 1):
        traffic_display = r['traffic_remain'] + r['traffic_detail']
        lines.append(f"👤 账号 [{idx}]: {r['safe_email']}")
        lines.append(f"📌 签到状态: {r['status_text']}")
        lines.append(f"📶 剩余流量: {traffic_display}")
        lines.append(f"⏳ 账户状态: {r['expire_status']}")
        if idx < total_count:
            lines.append("─" * 32)

    lines.append("═" * 32)
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 主程序入口
# ─────────────────────────────────────────────
def handler(event=None, context=None):
    try:
        # 1. 获取当前可用目标域名
        base_url = get_target_domain()
        print(f"\n当前生效的目标域名: {base_url}")

        # 2. 读取账号列表
        accounts_str = os.environ.get('ACCOUNTS')
        if not accounts_str:
            raise Exception('未配置 ACCOUNTS 环境变量，请在 GitHub Secrets 中设置')

        accounts = []
        for line in accounts_str.strip().splitlines():
            line = line.strip()
            if line and ':' in line:
                email, passwd = line.split(':', 1)
                accounts.append((email.strip(), passwd.strip()))

        if not accounts:
            raise Exception('未从 ACCOUNTS 环境变量中读取到有效账号（请按 邮箱:密码 格式配置）')

        print(f'\n共发现 {len(accounts)} 个账号，开始执行签到流程')

        records = []
        # 逐个账号执行签到
        for idx, (email, passwd) in enumerate(accounts, 1):
            print('\n' + '=' * 50)
            print(f'开始处理第 {idx} / {len(accounts)} 个账号')
            print('=' * 50)
            rec = checkin_one_account(email, passwd, base_url)
            records.append(rec)

        # 3. 生成排版消息
        message = build_notification_message(records, base_url)
        print('\n' + '=' * 20 + ' 推送消息预览 ' + '=' * 20)
        print(message)

        # 4. 派发通知
        title = "IKUUU 机场签到通知"
        dispatch_notifications(title, message)

    except Exception as e:
        err_msg = f"签到任务异常中断：{str(e)}"
        print(err_msg)
        dispatch_notifications("IKUUU 机场签到异常", f"【IKUUU 签到异常】\n{err_msg}")

    return '任务完成'


if __name__ == "__main__":
    handler()
