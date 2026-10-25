# -*- coding: utf-8 -*-
"""Bilibili QR login helpers. The UI owns polling and displays the generated QR."""
import time
import requests
from bili_api import PASSPORT, API, UA, new_session, BiliAPIError

def qr_generate(session):
    r = session.get(PASSPORT+'/x/passport-login/web/qrcode/generate', timeout=15)
    r.raise_for_status()
    j = r.json()
    d = j.get('data') or {}
    if j.get('code') != 0 or not d.get('url') or not d.get('qrcode_key'):
        raise BiliAPIError(j.get('message') or 'B站未返回有效二维码')
    return d['url'], d['qrcode_key']

def qr_poll(session, key):
    r = session.get(PASSPORT+'/x/passport-login/web/qrcode/poll', params={'qrcode_key':key}, timeout=15)
    r.raise_for_status()
    j = r.json()
    d = j.get('data') or {}
    return int(d.get('code',-1)), d.get('message') or j.get('message') or ''

def cookie_header(session):
    parts = []
    for c in session.cookies:
        try:
            parts.append('%s=%s' % (c.name,c.value))
        except Exception:
            continue
    return '; '.join(parts)

def validate_login(session):
    r = session.get(API+'/x/web-interface/nav', timeout=15)
    r.raise_for_status()
    j = r.json()
    d = j.get('data') or {}
    if j.get('code') != 0 or not d.get('isLogin'):
        raise BiliAPIError('扫码成功但登录态校验失败')
    cookie = cookie_header(session)
    if 'SESSDATA=' not in cookie:
        raise BiliAPIError('B站没有返回有效会话 Cookie，请重新扫码')
    return cookie, {
        'uname':d.get('uname','B站用户'),
        'mid':d.get('mid',0),
        'vipType':d.get('vipType',0),
        'face':d.get('face','')
    }
