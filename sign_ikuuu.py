from playwright.sync_api import sync_playwright
import requests
import os
import sys
import time
from wxmsg import send_wx

# 保证标准输出支持 UTF-8（防止 Windows 控制台因输出 ✅/❌ 抛出 gbk 编码错误）
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 企业微信配置
corpid = os.environ.get('WX_CORPID') or ''
corpsecret = os.environ.get('WX_CORPSECRET') or ''
agentid = os.environ.get('WX_AGENTID') or ''

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)

def send_webhook(content, webhook_url):
    if not webhook_url:
        return
    try:
        resp = requests.post(
            webhook_url,
            json={"msgtype": "text", "text": {"content": content}},
            timeout=9,
        ).json()
        print(f"[群机器人通知] {resp}")
    except Exception as e:
        print(f"[群机器人通知失败] {e}")

# ─────────────────────────────
# 邮箱脱敏
# ─────────────────────────────
def mask_email(email):
    """
    abc123@gmail.com
    -> a****3@gmail.com
    """

    if '@' not in email:
        return email

    name, domain = email.split('@', 1)

    if len(name) <= 1:
        masked = name

    elif len(name) == 2:
        masked = name[0] + '*'

    else:
        masked = (
            name[0]
            + '*' * (len(name) - 2)
            + name[-1]
        )

    return f'{masked}@{domain}'


# ─────────────────────────────
# 自动获取与探测最新可用域名
# ─────────────────────────────
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
            # 必须排除“最新域名”导航发布页，且确认含有实际系统的登录标识
            if ('originBody' in text or 'login' in text.lower()) and '最新域名' not in text:
                return True
    except Exception:
        pass
    return False

def extract_domains_from_landing_page(landing_url):
    """使用 Playwright 访问发布页，动态解析最新域名列表"""
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
            import re
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
    """获取当前可用的 IKUUU 主域名（支持环境变量优先、发布页解析、备用池和可用性探测）"""
    # 1. 优先使用用户自定义的环境变量
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

    # 2. 从发布页动态爬取最新域名
    for landing_page in LANDING_PAGES:
        print(f"[域名解析] 正在访问官方发布页获取最新域名: {landing_page}")
        extracted = extract_domains_from_landing_page(landing_page)
        if extracted:
            print(f"[域名解析] 从发布页成功提取到域名: {extracted}")
            candidates.extend(extracted)
            break

    # 3. 合并内置备用池并去重
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


# ─────────────────────────────
# Playwright 登录获取 Cookie
# ─────────────────────────────
def playwright_login(email, passwd, base_url):

    safe_email = mask_email(email)

    print(f'\n启动浏览器进行登录：{safe_email}')

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled'
            ]
        )

        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN"
        )

        # 隐藏自动化特征
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        page = context.new_page()

        # 打开登录页
        login_url = f'{base_url}/auth/login'
        print(f'正在打开登录页: {login_url}')
        page.goto(
            login_url,
            wait_until='networkidle'
        )

        print('填写账号密码...')

        # 输入账号密码
        page.fill('#email', email)
        page.fill('#password', passwd)

        print('点击验证按钮...')

        # 点击 “点我开始验证”
        try:

            page.click('.geetest_btn_click', timeout=5000)

            print('验证按钮点击成功')

        except:

            print('未找到验证按钮，继续登录')

        time.sleep(2)

        print('点击登录按钮...')

        # 点击登录
        page.click('button[type="submit"]')

        # 等待跳转
        time.sleep(5)

        print('获取 Cookie...')

        # 获取浏览器 Cookie
        cookies = context.cookies()

        browser.close()

        return cookies


# ─────────────────────────────
# 单账号签到
# ─────────────────────────────
def checkin_one_account(email, passwd, base_url):

    safe_email = mask_email(email)

    check_url = f'{base_url}/user/checkin'

    header = {
        'origin': base_url,
        'referer': f'{base_url}/user',
        'user-agent': USER_AGENT
    }

    try:

        # 登录获取 Cookie
        pw_cookies = playwright_login(email, passwd, base_url)

        if not pw_cookies:
            raise Exception('未获取到 Cookie')

        print('创建 requests session...')

        session = requests.session()

        # 导入 Cookie
        for c in pw_cookies:

            name = c.get('name')
            value = c.get('value')

            if name and value:
                session.cookies.set(name, value)

        print('开始签到...')

        # requests 执行签到
        result = session.post(
            url=check_url,
            headers=header,
            timeout=20
        ).json()

        print(result)

        content = result.get('msg', '未知结果')

        print(content)

        return f'✅ {safe_email} -> {content}'

    except Exception as e:

        err = f'❌ {safe_email} -> {str(e)}'

        print(err)

        return err


# ─────────────────────────────
# 主函数
# ─────────────────────────────
def handler(event=None, context=None):

    try:

        # 1. 自动获取当前可用目标域名
        base_url = get_target_domain()
        print(f"\n当前生效的目标域名: {base_url}")

        # 2. 多账号环境变量
        # 格式：
        # aaa@qq.com:123456
        # bbb@qq.com:abcdef

        accounts_str = os.environ.get('ACCOUNTS')

        if not accounts_str:
            raise Exception('未配置 ACCOUNTS 环境变量')

        accounts = []

        for line in accounts_str.strip().splitlines():

            line = line.strip()

            if line and ':' in line:

                email, passwd = line.split(':', 1)

                accounts.append(
                    (email.strip(), passwd.strip())
                )

        if not accounts:
            raise Exception('未读取到账号')

        print(f'\n共发现 {len(accounts)} 个账号')

        all_result = []

        # 逐个账号签到
        for idx, (email, passwd) in enumerate(accounts, 1):

            print('\n' + '=' * 50)
            print(f'开始处理第 {idx} 个账号')
            print('=' * 50)

            result = checkin_one_account(email, passwd, base_url)

            all_result.append(result)

        # 汇总结果
        final_msg = '\n'.join(all_result)

        print('\n最终结果：')
        print(final_msg)

        # 消息通知
        notify_msg = f"[ikuuu] 多账号签到结果：\n{final_msg}"
        send_wx(notify_msg, corpid, corpsecret, agentid)
        send_webhook(notify_msg, os.environ.get('WX_WEBHOOK') or '')

    except Exception as e:

        content = f'签到失败：{str(e)}'

        print(content)

        notify_msg = f"[ikuuu] 签到结果：{content}"
        send_wx(notify_msg, corpid, corpsecret, agentid)
        send_webhook(notify_msg, os.environ.get('WX_WEBHOOK') or '')

    return '任务完成'


if __name__ == "__main__":
    handler()
