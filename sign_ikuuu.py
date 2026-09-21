# -*- coding: utf-8 -*-
"""
IKUUU 机场多账号自动签到脚本
特性：
1. 自动获取与探测 IKUUU 官方最新可用域名
2. Playwright 真实浏览器无头自动化登录，绕过前端验证风控
3. 登录后自动访问用户中心提取渲染后页面，查询剩余流量与剩余天数
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
        parts = [p.strip() for p in re.split(r'[,;:]', raw) if p.strip()]
        if len(parts) >= 3:
            return parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            return parts[0], parts[1], ''

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
            errcode = token_resp.get('errcode')
            errmsg = token_resp.get('errmsg', '')
            print(f"[企业微信应用通知提示] 获取 access_token 响应: {token_resp}")
            if errcode == 60020:
                print("💡 提示：企业微信后台开启了【企业可信IP】白名单，GitHub Actions 云端动态IP被拦截。群机器人或 PushPlus 推送不受影响。")
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
    # 1. 企业微信自建应用
    corpid, corpsecret, agentid = get_wechat_config()
    if corpid and corpsecret and agentid:
        print("\n[消息推送] 正在发送企业微信应用通知...")
        send_wechat_app(message, corpid, corpsecret, agentid)

    # 2. 企业微信群机器人
    webhook_url = os.environ.get('WX_WEBHOOK') or ''
    if webhook_url:
        print("[消息推送] 正在发送企业微信群机器人通知...")
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
    'https://ikuuu.club',
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
    for landing_page in LANDING_PAGES:
        print(f"[域名解析] 正在访问官方发布页获取最新域名: {landing_page}")
        extracted = extract_domains_from_landing_page(landing_page)
        if extracted:
            print(f"[域名解析] 从发布页成功提取到域名: {extracted}")
            candidates.extend(extracted)
            break

    candidates.extend(FALLBACK_DOMAINS)
    seen = set()
    unique_candidates = [d for d in candidates if not (d in seen or seen.add(d))]
    print(f"[域名解析] 待探测候选域名列表: {unique_candidates}")

    for domain in unique_candidates:
        print(f"[域名解析] 正在探测域名可用性: {domain} ...")
        if is_valid_login_domain(domain):
            print(f"[域名解析] 成功选定可用主域名: {domain}")
            return domain

    fallback = 'https://ikuuu.top'
    print(f"[域名解析] 所有探测未响应，使用保底域名: {fallback}")
    return fallback


# ─────────────────────────────────────────────
# 账户剩余流量与剩余天数文本解析
# ─────────────────────────────────────────────
def parse_traffic_to_gb(traffic_str):
    """将各类流量单位（TB/GB/MB/KB/B）统一转换为 GB 浮点数"""
    if not traffic_str:
        return 0.0
    m = re.search(r'([0-9.]+)\s*([KMGT]?B)', traffic_str, re.I)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == 'TB':
        return val * 1024
    elif unit == 'GB':
        return val
    elif unit == 'MB':
        return val / 1024
    elif unit == 'KB':
        return val / (1024 * 1024)
    elif unit == 'B':
        return val / (1024 * 1024 * 1024)
    return val


def format_gb(gb_val):
    """将 GB 浮点数格式化为最适可读单位"""
    if gb_val >= 1024:
        return f"{gb_val / 1024:.2f} TB"
    elif gb_val >= 1:
        return f"{gb_val:.2f} GB"
    elif gb_val >= 0.001:
        return f"{gb_val * 1024:.2f} MB"
    else:
        return f"{gb_val * 1024 * 1024:.2f} KB"


def parse_account_details(rendered_text, html_content=""):
    """
    从浏览器渲染后的文本与 HTML 中解析出剩余流量、已用流量与账户有效期
    支持饼图卡片结构提取：
    - 可用 (如 168.59GB)
    - 今日已用 (如 3.23GB)
    - 已用 (如 174.61GB)
    总流量 = 可用 + 今日已用 + 已用 (所有部分全部加起来)
    """
    info = {
        'traffic_remain': '未知',
        'traffic_used': '',
        'traffic_total': '',
        'expire_status': '未知',
        'expire_days': None
    }
    content = f"{rendered_text}\n{html_content}"

    # 1. 提取可用/剩余流量
    m_remain = re.search(r'(?:可用|剩余流量|剩余可用|剩余|未使用)[^\d\n]*[:：\s]*([0-9.]+\s*(?:KB|MB|GB|TB|B))', content, re.I)
    if m_remain:
        info['traffic_remain'] = m_remain.group(1).strip()

    # 2. 提取今日已用
    m_today = re.search(r'今日已用[^\d\n]*[:：\s]*([0-9.]+\s*(?:KB|MB|GB|TB|B))', content, re.I)
    today_str = m_today.group(1).strip() if m_today else ''

    # 3. 提取非今日的已用流量（饼图中的“已用”或“过去已用”）
    m_past = re.search(r'(?:过去已用|(?<!今日)已用)[^\d\n]*[:：\s]*([0-9.]+\s*(?:KB|MB|GB|TB|B))', content, re.I)
    past_str = m_past.group(1).strip() if m_past else ''

    # 计算各分项数值 (GB)
    remain_gb = parse_traffic_to_gb(info['traffic_remain'])
    today_gb = parse_traffic_to_gb(today_str)
    past_gb = parse_traffic_to_gb(past_str)

    # 计算总已用流量与总流量
    used_total_gb = today_gb + past_gb
    if used_total_gb > 0:
        info['traffic_used'] = format_gb(used_total_gb)
    elif past_gb > 0:
        info['traffic_used'] = format_gb(past_gb)
    elif today_gb > 0:
        info['traffic_used'] = format_gb(today_gb)

    # 总流量 = 可用(剩余) + 今日已用 + 历史已用 (所有部分全部加起来)
    total_sum_gb = remain_gb + today_gb + past_gb
    if total_sum_gb > 0:
        info['traffic_total'] = format_gb(total_sum_gb)

    # 2. 提取到期时间与天数
    if re.search(r'(?:永久有效|无限期|长期有效)', content):
        info['expire_status'] = '永久有效'
    else:
        exp_pats = [
            r'(?:等级过期时间|账户到期时间|会员到期时间|到期时间|服务到期|有效期至)[^\d\n]*[:：\s]*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:\s+[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?)',
            r'([0-9]{4}-[0-9]{2}-[0-9]{2})\s*(?:到期|过期)',
            r'class_expire[\'":\s]+[\'"]?([0-9]{4}-[0-9]{2}-[0-9]{2}[^\'"]*)[\'"]?'
        ]
        for pat in exp_pats:
            m = re.search(pat, content, re.I)
            if m:
                expire_str = m.group(1).strip()
                try:
                    date_part = expire_str.split()[0]
                    exp_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                    now_date = datetime.now().date()
                    diff = (exp_date - now_date).days
                    info['expire_days'] = diff
                    if diff >= 0:
                        info['expire_status'] = f"剩余 {diff} 天 ({date_part})"
                    else:
                        info['expire_status'] = f"已过期 {abs(diff)} 天 ({date_part})"
                except Exception:
                    info['expire_status'] = expire_str
                break

        if info['expire_status'] == '未知':
            m_days = re.search(r'(?:距离到期还有|剩余)\s*(\d+)\s*天', content)
            if m_days:
                days = int(m_days.group(1))
                info['expire_days'] = days
                info['expire_status'] = f"剩余 {days} 天"

    return info


# ─────────────────────────────────────────────
# Playwright 登录并提取渲染后数据与 Cookie
# ─────────────────────────────────────────────
def playwright_login_and_fetch_info(email, passwd, base_url):
    safe_email = mask_email(email)
    print(f'\n启动浏览器登录并获取数据：{safe_email}')

    user_info = {
        'traffic_remain': '未知',
        'traffic_used': '',
        'traffic_total': '',
        'expire_status': '未知',
        'expire_days': None
    }
    cookies = []

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
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        page = context.new_page()

        # 1. 打开登录页
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
        print('点击登录提交按钮...')
        page.click('button[type="submit"]')

        # 2. 等待登录响应与页面跳转
        time.sleep(5)

        # 3. 在浏览器内直接访问用户中心 /user 以保证获取完整渲染内容
        user_url = f"{base_url}/user"
        print(f'正在访问用户中心页面读取账户信息: {user_url}')
        try:
            page.goto(user_url, wait_until='networkidle', timeout=15000)
            time.sleep(2)
            rendered_text = page.inner_text('body')
            html_content = page.content()
            user_info = parse_account_details(rendered_text, html_content)
        except Exception as e:
            print(f"[读取用户中心页面异常] {e}")

        cookies = context.cookies()
        browser.close()

    return cookies, user_info


# ─────────────────────────────────────────────
# 单账号签到与信息汇总
# ─────────────────────────────────────────────
def checkin_one_account(email, passwd, base_url):
    safe_email = mask_email(email)
    check_url = f'{base_url}/user/checkin'

    header = {
        'origin': base_url,
        'referer': f'{base_url}/user',
        'user-agent': USER_AGENT
    }

    record = {
        'real_email': email.strip(),
        'safe_email': safe_email,
        'success': False,
        'status_text': '未知',
        'traffic_remain': '未知',
        'traffic_detail': '',
        'expire_status': '未知'
    }

    try:
        # 1. 登录并提取页面资产数据
        pw_cookies, user_info = playwright_login_and_fetch_info(email, passwd, base_url)
        if not pw_cookies:
            raise Exception('未获取到 Cookie，可能登录失败或被拦截')

        session = requests.session()
        for c in pw_cookies:
            name = c.get('name')
            value = c.get('value')
            if name and value:
                session.cookies.set(name, value)

        # 2. 发起签到请求
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

        # 3. 关联账户流量与有效期数据
        record['traffic_remain'] = user_info['traffic_remain']
        record['traffic_used'] = user_info.get('traffic_used', '').strip()
        record['traffic_total'] = user_info.get('traffic_total', '').strip()
        record['expire_status'] = user_info['expire_status']

        print(f"账号 {safe_email} 汇总: {record['status_text']} | 剩余: {record['traffic_remain']} | 已用: {record['traffic_used']} | 总计: {record['traffic_total']} | 到期: {record['expire_status']}")

    except Exception as e:
        record['success'] = False
        record['status_text'] = f"❌ 失败: {str(e)}"
        print(f"账号 {safe_email} 处理异常: {e}")

    return record


# ─────────────────────────────────────────────
# 推送消息模版构建（专为手机微信端优化）
# ─────────────────────────────────────────────
def simplify_status(raw_status):
    """精炼签到状态文本，避免手机端长句被强行折行"""
    if '已经签到' in raw_status or '已签到' in raw_status:
        return '今日已签到'
    m = re.search(r'获得[^\d]*([0-9.]+\s*[KMGT]?B)', raw_status)
    if m:
        return f"签到成功 (+{m.group(1).strip()})"
    if '成功' in raw_status:
        return '签到成功'
    if '失败' in raw_status:
        return raw_status.replace('❌', '').replace('失败:', '').strip() or '签到失败'
    return raw_status.strip()


def build_notification_message(records, base_url, masked=False):
    """
    专为移动端（手机微信）优化的极简紧凑消息模版：
    1. 彻底去除长分割线（避免手机窄屏折行断裂）
    2. 字段扁平垂直展示，短小精悍，保证零折行
    3. 支持单账号极简与多账号清晰分块
    :param masked: True 为 GitHub Actions 日志脱敏加密模式，False 为推送到企业微信/PushPlus 的明文真实账号模式
    """
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total = len(records)

    lines = ["✈️ iKuuu 签到通知", "──────────────"]

    for idx, r in enumerate(records, 1):
        account = r['safe_email'] if masked else r['real_email']
        status = simplify_status(r['status_text'])
        remain = r.get('traffic_remain', '未知')
        used = r.get('traffic_used', '')
        total_traffic = r.get('traffic_total', '')
        expire = r.get('expire_status', '未知')

        if total > 1:
            lines.append(f"【账号 {idx}】{account}")
        else:
            lines.append(f"👤 账号: {account}")

        lines.append(f"📌 状态: {status}")
        lines.append(f"📶 剩余: {remain}")
        if used and used != '未知':
            lines.append(f"📊 已用: {used}")
        if total_traffic and total_traffic != '未知':
            lines.append(f"📦 总量: {total_traffic}")
        lines.append(f"⏳ 到期: {expire}")

        if idx < total:
            lines.append("──────────────")

    lines.append("──────────────")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 主程序入口
# ─────────────────────────────────────────────
def handler(event=None, context=None):
    try:
        base_url = get_target_domain()
        print(f"\n当前生效的目标域名: {base_url}")

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
        for idx, (email, passwd) in enumerate(accounts, 1):
            print('\n' + '=' * 50)
            print(f'开始处理第 {idx} / {len(accounts)} 个账号')
            print('=' * 50)
            rec = checkin_one_account(email, passwd, base_url)
            records.append(rec)

        # 1. GitHub Actions 日志输出：账号全脱敏加密保护隐私
        log_message = build_notification_message(records, base_url, masked=True)
        print('\n' + '=' * 20 + ' Actions 日志预览(已脱敏加密) ' + '=' * 20)
        print(log_message)

        # 2. 外部通知推送（企业微信/PushPlus）：使用真实明文账号方便识别
        push_message = build_notification_message(records, base_url, masked=False)
        title = "IKUUU 机场签到通知"
        dispatch_notifications(title, push_message)

    except Exception as e:
        err_msg = f"签到任务异常中断：{str(e)}"
        print(err_msg)
        dispatch_notifications("IKUUU 机场签到异常", f"【IKUUU 签到异常】\n{err_msg}")

    return '任务完成'


if __name__ == "__main__":
    handler()
