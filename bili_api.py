# -*- coding: utf-8 -*-
"""Bilibili web API helpers. No credentials are sent to any third-party server."""
import re
import time
import urllib.parse
import requests

API = 'https://api.bilibili.com'
PASSPORT = 'https://passport.bilibili.com'
UA = ('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36')

class BiliAPIError(RuntimeError):
    pass

def extract_video_id(text):
    text = (text or '').strip()
    bv = re.search(r'BV[0-9A-Za-z]{10}', text, re.I)
    if bv:
        return {'kind':'BV', 'id':bv.group(0), 'url':'https://www.bilibili.com/video/'+bv.group(0)}
    av = re.search(r'(?i)(?:av\s*|/video/av)(\d+)', text)
    if av:
        return {'kind':'AV', 'id':'av'+av.group(1), 'url':'https://www.bilibili.com/video/av'+av.group(1)}
    u = re.search(r'https?://(?:b23\.tv|bili2233\.cn)/[^\s，。；、<>\"\']+', text, re.I)
    if u:
        short_url=u.group(0).rstrip('.,;!?)]}，。；、！？”’')
        return {'kind':'SHORT', 'id':short_url, 'url':short_url}
    try:
        parsed = urllib.parse.urlparse(text if '://' in text else 'https://'+text)
        host = (parsed.hostname or '').lower()
        if host.endswith('bilibili.com') and '/video/' in parsed.path:
            return {'kind':'URL', 'id':parsed.path.rsplit('/',1)[-1], 'url':text if '://' in text else 'https://'+text}
    except Exception:
        pass
    raise ValueError('无法识别链接。请粘贴 BV/AV 号或 B 站视频/分享链接。')

def new_session(cookie=''):
    s = requests.Session()
    s.headers.update({'User-Agent':UA, 'Referer':'https://www.bilibili.com/',
                      'Accept':'application/json, text/plain, */*'})
    if cookie:
        for item in cookie.split(';'):
            if '=' in item:
                k,v = item.strip().split('=',1)
                s.cookies.set(k.strip(), v.strip(), domain='.bilibili.com')
    return s

def resolve_short(session, url):
    r = session.get(url, allow_redirects=True, timeout=20)
    r.raise_for_status()
    m = re.search(r'BV[0-9A-Za-z]{10}', r.url, re.I)
    if m:
        return {'kind':'BV','id':m.group(0),'url':'https://www.bilibili.com/video/'+m.group(0)}
    m = re.search(r'av(\d+)', r.url, re.I)
    if m:
        return {'kind':'AV','id':'av'+m.group(1),'url':'https://www.bilibili.com/video/av'+m.group(1)}
    m = re.search(r'BV[0-9A-Za-z]{10}', r.text, re.I)
    if m:
        return {'kind':'BV','id':m.group(0),'url':'https://www.bilibili.com/video/'+m.group(0)}
    raise BiliAPIError('短链接无法解析，请尝试直接粘贴跳转后的 B 站视频链接。')

def _api_json(session, url, params=None):
    r = session.get(url, params=params, timeout=20)
    r.raise_for_status()
    try: payload = r.json()
    except Exception as e: raise BiliAPIError('B站接口返回了无法识别的数据') from e
    if payload.get('code') != 0:
        raise BiliAPIError('B站接口错误 %s：%s' % (payload.get('code'), payload.get('message') or payload.get('msg') or '未知错误'))
    return payload.get('data') or {}

def get_nav(session):
    data = _api_json(session, API+'/x/web-interface/nav')
    return data if data.get('isLogin') else None

def get_video_info(session, url_or_id):
    ref = extract_video_id(url_or_id)
    if ref['kind'] == 'SHORT': ref = resolve_short(session, ref['url'])
    params = {'bvid':ref['id']} if ref['kind'] in ('BV','URL') and ref['id'].upper().startswith('BV') else {'aid':re.sub(r'\D','',ref['id'])}
    view = _api_json(session, API+'/x/web-interface/view', params)
    pages = view.get('pages') or []
    page = pages[0] if pages else {}
    return {
        'bvid':view.get('bvid',''), 'aid':view.get('aid'), 'cid':page.get('cid'),
        'title':view.get('title','(无标题)'), 'description':view.get('desc',''),
        'owner':(view.get('owner') or {}).get('name','未知UP主'),
        'mid':(view.get('owner') or {}).get('mid'), 'duration':view.get('duration',0),
        'pic':view.get('pic',''), 'view':(view.get('stat') or {}).get('view',0),
        'danmaku':(view.get('stat') or {}).get('danmaku',0), 'like':(view.get('stat') or {}).get('like',0),
        'coin':(view.get('stat') or {}).get('coin',0), 'favorite':(view.get('stat') or {}).get('favorite',0),
        'reply':(view.get('stat') or {}).get('reply',0), 'pages':pages,
        'url':'https://www.bilibili.com/video/'+view.get('bvid','')
    }

def _wbi_mixin_key(session):
    # Official web-client WBI key derivation. If the key endpoint changes,
    # caller falls back to the legacy playurl endpoint below.
    data = _api_json(session, API + '/x/web-interface/nav')
    wbi = data.get('wbi_img') or {}
    img = (wbi.get('img_url') or '').rsplit('/', 1)[-1].split('.')[0]
    sub = (wbi.get('sub_url') or '').rsplit('/', 1)[-1].split('.')[0]
    source = img + sub
    order = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
             27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
             37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
             22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]
    return ''.join(source[i] for i in order if i < len(source))[:32]

def _signed_playurl(session, info, qn):
    import hashlib
    mixin = _wbi_mixin_key(session)
    params = {'cid': int(info['cid']), 'qn': int(qn), 'fnval': 4048, 'fnver': 0, 'fourk': 1, 'wts': int(time.time())}
    if info.get('bvid'): params['bvid'] = info['bvid']
    else: params['avid'] = int(info.get('aid'))
    params = {k: ''.join(ch for ch in str(v) if ch not in "!'()*") for k, v in params.items()}
    query = '&'.join('%s=%s' % (urllib.parse.quote(k, safe=''), urllib.parse.quote(str(params[k]), safe='')) for k in sorted(params))
    params['w_rid'] = hashlib.md5((query + mixin).encode('utf-8')).hexdigest()
    return _api_json(session, API + '/x/player/wbi/playurl', params)

def get_play_info(session, info, cookie=''):
    """Return actual playable qualities from Bilibili's web playurl API."""
    data = None
    try:
        data = _signed_playurl(session, info, 127)
    except Exception:
        # Compatibility fallback for deployments/videos where legacy API remains enabled.
        params = {'cid':info['cid'], 'qn':127, 'fnval':1, 'fnver':0, 'fourk':1}
        if info.get('bvid'): params['bvid'] = info['bvid']
        else: params['avid'] = info.get('aid')
        data = _api_json(session, API+'/x/player/playurl', params)
    accepted = data.get('accept_quality') or []
    descriptions = data.get('accept_description') or []
    quality_map = {}
    for q,d in zip(accepted, descriptions): quality_map[int(q)] = str(d)
    return {'data':data, 'qualities':quality_map, 'format':('dash' if data.get('dash') else 'durl')}

def format_duration(seconds):
    try: seconds = int(seconds)
    except Exception: return '未知'
    return '%02d:%02d:%02d' % (seconds//3600, (seconds%3600)//60, seconds%60) if seconds >= 3600 else '%02d:%02d' % (seconds//60, seconds%60)


def get_stream_sources(session, info, qn=0):
    """
    返回可下载的播放源，统一结构，供在线播放使用。
    优先尝试一体流 (durl)，否则返回 DASH 的 video + audio。
    """
    qn = int(qn) if str(qn).isdigit() else 0
    data = None
    try:
        data = _signed_playurl(session, info, qn or 127)
    except Exception:
        params = {
            'cid': info['cid'], 'qn': qn or 80,
            'fnval': 16, 'fnver': 0, 'fourk': 1
        }
        if info.get('bvid'):
            params['bvid'] = info['bvid']
        else:
            params['avid'] = info.get('aid')
        data = _api_json(session, API + '/x/player/playurl', params)

    # 1) 一体流
    durl = data.get('durl') or []
    if durl:
        item = durl[0]
        urls = [item.get('url')] + list(item.get('backup_url') or [])
        return {
            'type': 'durl',
            'urls': [u for u in urls if u],
            'quality': int(data.get('quality') or qn or 0),
        }

    # 2) DASH
    dash = data.get('dash') or {}
    videos = dash.get('video') or []
    audios = dash.get('audio') or []
    if not videos:
        raise BiliAPIError('没有可用的视频流')

    video = None
    if qn:
        video = next((v for v in videos if int(v.get('id', 0)) == qn), None)
    if video is None:
        video = sorted(videos, key=lambda v: int(v.get('id', 0)), reverse=True)[0]

    def stream_urls(s):
        first = s.get('baseUrl') or s.get('base_url')
        backup = s.get('backupUrl') or s.get('backup_url') or []
        return [u for u in ([first] + list(backup)) if u]

    result = {
        'type': 'dash',
        'video_urls': stream_urls(video),
        'audio_urls': stream_urls(audios[0]) if audios else [],
        'quality': int(video.get('id', 0)),
    }
    if not result['audio_urls']:
        raise BiliAPIError('没有可用的音频流，无法在线播放')
    return result
