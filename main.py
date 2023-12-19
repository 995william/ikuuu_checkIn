import requests, json, re, os, time

session = requests.session()

# url = 'https://ikuuu.me'
url = os.environ.get('URL')
login_url = '{}/auth/login'.format(url)
check_url = '{}/user/checkin'.format(url)
logout_url = '{}/user/logout'.format(url)

msg_template = 'http://www.pushplus.plus/send?token={}&title=ikuuu签到&content={}'

header = {
        'origin': 'https://ikuuu.eu',
        'user-agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'
}
def checkIn(email, passwd, SCKEY):
    data = {
            'email': email,
            'passwd': passwd
    }
    try:
        print('进行登录...')
        response = json.loads(session.post(url=login_url,headers=header,data=data).text)
        print(response['msg'])
        # 进行签到
        result = json.loads(session.post(url=check_url,headers=header).text)
        print(result['msg'])
        content = result['msg']
        # 进行推送
        if SCKEY != '':
            push_url = msg_template.format(SCKEY, content)
            requests.post(url=push_url)
            print('推送成功')
        session.get(logout_url)
    except:
        content = '签到失败'
        print(content)
        if SCKEY != '':
            push_url = msg_template.format(SCKEY, content)
            requests.post(url=push_url)
        session.get(logout_url)

if __name__ == '__main__':
    split = os.environ.get('INFO').split(',')
    for user in split:
        user_split = user.split('<split>')
        email = user_split[0]
        password = user_split[1]
        sckey = user_split[2]
        checkIn(email, password, sckey)
        time.sleep(2)
