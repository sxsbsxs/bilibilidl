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
    short_text = text if re.match(r'^https?://', text, re.I) else ('https://' + text if re.match(r'^(?:www\.)?(?:b23\.tv|bili2233\.cn)/', text, re.I) else text)
    u = re.search(r'https?://(?:www\.)?(?:b23\.tv|bili2233\.cn)/[^\s，。；、<>\"\']+', short_text, re.I)
    if u:
        short_url = u.group(0).rstrip('.,;!?)]}，。；、！？”’')
        return {'kind': 'SHORT', 'id': short_url, 'url': short_url}
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

def _normalize_cover_url(value):
    """Normalize legacy/protocol-relative Bilibili cover URLs."""
    value = str(value or '').strip()
    if value.startswith('//'):
        return 'https:' + value
    if value.startswith('http://'):
        return 'https://' + value[len('http://'):]
    return value

def get_video_info(session, url_or_id):
    ref = extract_video_id(url_or_id)
    if ref['kind'] == 'SHORT': ref = resolve_short(session, ref['url'])
    params = {'bvid':ref['id']} if ref['kind'] in ('BV','URL') and ref['id'].upper().startswith('BV') else {'aid':re.sub(r'\D','',ref['id'])}
    view = _api_json(session, API+'/x/web-interface/view', params)
    pages = view.get('pages') or []
    page = pages[0] if pages else {}
    cid = page.get('cid') or view.get('cid')
    if not pages and cid:
        pages = [{'cid': cid, 'page': 1, 'part': view.get('title', 'P1'),
                  'duration': view.get('duration', 0)}]
    return {
        'bvid':view.get('bvid',''), 'aid':view.get('aid'), 'cid':cid,
        'title':view.get('title','(无标题)'), 'description':view.get('desc',''),
        'owner':(view.get('owner') or {}).get('name','未知UP主'),
        'mid':(view.get('owner') or {}).get('mid'), 'duration':view.get('duration',0),
        'pic':_normalize_cover_url(view.get('pic')), 'view':(view.get('stat') or {}).get('view',0),
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
    if len(source) < 64:
        raise BiliAPIError('B站 WBI 签名密钥暂不可用')
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

# -------------------- Homepage / player / interaction API --------------------

def _api_data_flexible(session, url, params=None):
    """API helper for endpoints that may return useful data as None."""
    r = session.get(url, params=params, timeout=20)
    r.raise_for_status()
    try:
        payload = r.json()
    except Exception as exc:
        raise BiliAPIError('B站接口返回了无法识别的数据') from exc
    code = payload.get('code', 0)
    if code != 0:
        raise BiliAPIError('B站接口错误 %s：%s' % (
            code, payload.get('message') or payload.get('msg') or '未知错误'))
    return payload.get('data')


def _normalize_video(view):
    owner = view.get('owner') or {}
    stat = view.get('stat') or {}
    bvid = view.get('bvid') or ''
    pages = view.get('pages') or []
    return {
        'bvid': bvid, 'aid': view.get('aid'), 'cid': (pages[0].get('cid') if pages else None),
        'title': view.get('title') or '(无标题)',
        'description': view.get('desc') or '',
        'owner': owner.get('name') or '未知UP主', 'mid': owner.get('mid'),
        'duration': view.get('duration') or 0, 'pic': _normalize_cover_url(view.get('pic')),
        'view': stat.get('view', 0), 'danmaku': stat.get('danmaku', 0),
        'like': stat.get('like', 0), 'coin': stat.get('coin', 0),
        'favorite': stat.get('favorite', 0), 'reply': stat.get('reply', 0),
        'pages': pages, 'url': 'https://www.bilibili.com/video/' + bvid,
    }


def get_popular_videos(session, page=1, page_size=20):
    """Return normalized public popular videos for a homepage feed."""
    page = max(1, int(page or 1))
    page_size = min(50, max(1, int(page_size or 20)))
    data = _api_data_flexible(
        session, API + '/x/web-interface/popular',
        {'pn': page, 'ps': page_size})
    if not isinstance(data, dict):
        return []
    result = []
    for item in data.get('list') or []:
        # Popular API normally returns archive fields directly; some variants wrap them.
        view = item.get('archive') if isinstance(item.get('archive'), dict) else item
        normalized = _normalize_video(view)
        if normalized.get('bvid'):
            result.append(normalized)
    return result


def get_recommended_videos(session, page=1, page_size=20):
    """Compatibility alias for the homepage feed."""
    return get_popular_videos(session, page, page_size)


def get_video_detail(session, url_or_id):
    """Return normalized video detail, matching get_video_info's data shape."""
    return get_video_info(session, url_or_id)


def get_comments(session, info_or_aid, page=1, page_size=20, sort=2):
    """Fetch public top-level comments for a video."""
    aid = info_or_aid.get('aid') if isinstance(info_or_aid, dict) else info_or_aid
    if not aid:
        raise BiliAPIError('缺少视频 aid，无法获取评论')
    data = _api_data_flexible(session, API + '/x/v2/reply', {
        'type': 1, 'oid': int(aid), 'pn': max(1, int(page or 1)),
        'ps': min(49, max(1, int(page_size or 20))), 'sort': int(sort or 2),
    }) or {}
    replies = data.get('replies') or []
    output = []
    for item in replies:
        member = item.get('member') or {}
        content = item.get('content') or {}
        output.append({
            'rpid': item.get('rpid'), 'mid': member.get('mid'),
            'user': member.get('uname') or '用户',
            'avatar': member.get('avatar') or '',
            'message': content.get('message') or '',
            'like': item.get('like', 0), 'ctime': item.get('ctime', 0),
        })
    return output


def get_danmaku(session, cid, limit=1000):
    """Fetch danmaku XML and return parsed timed entries."""
    import xml.etree.ElementTree as ET
    if not cid:
        raise BiliAPIError('缺少视频 cid，无法获取弹幕')
    url = 'https://comment.bilibili.com/%s.xml' % int(cid)
    response = session.get(url, timeout=25)
    response.raise_for_status()
    try:
        root = ET.fromstring(response.content)
    except Exception as exc:
        raise BiliAPIError('弹幕 XML 解析失败') from exc
    output = []
    for node in root.findall('.//d'):
        p = (node.get('p') or '').split(',')
        if len(p) < 5:
            continue
        try:
            seconds = float(p[0])
            mode = int(p[1])
            font_size = int(p[2])
            color = int(p[3])
        except (TypeError, ValueError):
            continue
        output.append({
            'time': seconds, 'mode': mode, 'size': font_size,
            'color': color, 'text': node.text or '',
        })
        if len(output) >= max(1, int(limit or 1000)):
            break
    return output


def get_danmaku_xml_url(cid):
    if not cid:
        raise BiliAPIError('缺少视频 cid')
    return 'https://comment.bilibili.com/%s.xml' % int(cid)


def get_stream_urls(session, info, quality=80):
    """Return direct stream metadata; DASH video/audio are separate streams."""
    if not isinstance(info, dict):
        raise BiliAPIError('视频信息格式不正确')
    play = get_play_info(session, info)
    data = play.get('data') or {}
    dash = data.get('dash') or {}
    videos = dash.get('video') or []
    audios = dash.get('audio') or []
    qn = int(quality or 80)
    videos = [item for item in videos if isinstance(item, dict)]
    audios = [item for item in audios if isinstance(item, dict)]
    eligible = [item for item in videos if int(item.get('id') or 0) <= qn]
    chosen = max(eligible, key=lambda item: int(item.get('id') or 0)) if eligible else (
        max(videos, key=lambda item: int(item.get('id') or 0)) if videos else None)
    audio = max(audios, key=lambda item: int(item.get('id') or 0)) if audios else None
    if chosen:
        return {
            'format': 'dash', 'video_url': chosen.get('baseUrl') or chosen.get('base_url') or '',
            'audio_url': (audio or {}).get('baseUrl') or (audio or {}).get('base_url') or '',
            'mime_type': chosen.get('mimeType') or chosen.get('mime_type') or '',
            'quality': chosen.get('id'), 'play_info': play,
        }
    durl = data.get('durl') or []
    if durl:
        return {'format': 'durl', 'video_url': durl[0].get('url', ''),
                'audio_url': '', 'quality': data.get('quality'), 'play_info': play}
    raise BiliAPIError('B站没有返回可用播放地址；可能需要登录或视频不可播放')


def _csrf(session):
    try:
        value = session.cookies.get('bili_jct')
    except Exception:
        value = None
    if not value:
        # Some sessions use a manually supplied cookie jar.
        for cookie in getattr(session.cookies, 'jar', []):
            if cookie.name == 'bili_jct':
                value = cookie.value
                break
    if not value:
        raise BiliAPIError('此操作需要登录，请先完成 B 站授权登录')
    return value


def _post_action(session, path, data):
    csrf = _csrf(session)
    body = dict(data)
    body['csrf'] = csrf
    response = session.post(API + path, data=body, timeout=20)
    response.raise_for_status()
    try:
        payload = response.json()
    except Exception as exc:
        raise BiliAPIError('B站互动接口返回了无法识别的数据') from exc
    if payload.get('code') != 0:
        raise BiliAPIError('互动失败 %s：%s' % (
            payload.get('code'), payload.get('message') or payload.get('msg') or '未知错误'))
    return payload.get('data') or {}


def like_video(session, info_or_aid, like=True):
    aid = info_or_aid.get('aid') if isinstance(info_or_aid, dict) else info_or_aid
    bvid = info_or_aid.get('bvid') if isinstance(info_or_aid, dict) else None
    if not aid and not bvid:
        raise BiliAPIError('缺少视频标识')
    data = {'like': 1 if like else 2}
    if aid:
        data['aid'] = int(aid)
    if bvid:
        data['bvid'] = bvid
    return _post_action(session, '/x/web-interface/archive/like', data)


def coin_video(session, info_or_aid, multiply=1, select_like=False):
    aid = info_or_aid.get('aid') if isinstance(info_or_aid, dict) else info_or_aid
    if not aid:
        raise BiliAPIError('缺少视频 aid')
    amount = max(1, min(2, int(multiply or 1)))
    return _post_action(session, '/x/web-interface/coin/add', {
        'aid': int(aid), 'multiply': amount,
        'select_like': 1 if select_like else 0,
    })


def get_favorite_folders(session, mid=None, page=1, page_size=20):
    if mid is None:
        nav = get_nav(session)
        if not nav:
            raise BiliAPIError('请先登录 B 站')
        mid = nav.get('mid')
    data = _api_data_flexible(session, API + '/x/v3/fav/folder/created/list-all', {
        'up_mid': int(mid), 'type': 2,
    }) or {}
    folders = data.get('list') or []
    return [{
        'id': item.get('id'), 'title': item.get('title') or '收藏夹',
        'media_count': item.get('media_count', 0), 'attr': item.get('attr', 0),
    } for item in folders]


def favorite_video(session, info_or_aid, folder_id=None, add=True):
    """Add/remove a video in a chosen favorite folder (defaults to first folder)."""
    aid = info_or_aid.get('aid') if isinstance(info_or_aid, dict) else info_or_aid
    if not aid:
        raise BiliAPIError('缺少视频 aid')
    if folder_id is None:
        folders = get_favorite_folders(session)
        if not folders:
            raise BiliAPIError('没有可用的收藏夹，请先在 B 站创建收藏夹')
        folder_id = folders[0]['id']
    if not folder_id:
        raise BiliAPIError('收藏夹 ID 无效')
    return _post_action(session, '/x/v3/fav/resource/deal', {
        'rid': int(aid), 'type': 2, 'add_media_ids': str(int(folder_id)) if add else '',
        'del_media_ids': '' if add else str(int(folder_id)),
    })


def follow_user(session, mid, follow=True):
    if not mid:
        raise BiliAPIError('缺少 UP 主 mid')
    return _post_action(session, '/x/relation/modify', {
        'fid': int(mid), 'act': 1 if follow else 2, 're_src': 11,
    })


def get_subtitle_list(session, info):
    """Return subtitle tracks from player/v2, when available."""
    if not isinstance(info, dict) or not info.get('cid') or not info.get('bvid'):
        raise BiliAPIError('缺少视频信息，无法获取字幕')
    data = _api_data_flexible(session, API + '/x/player/v2', {
        'bvid': info['bvid'], 'cid': int(info['cid']),
    }) or {}
    return (data.get('subtitle') or {}).get('subtitles') or []

