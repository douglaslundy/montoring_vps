import ipaddress
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from models.database import IpGeoCache

GEO_API_URL = "http://ip-api.com/json/{ip}"
GEO_API_FIELDS = "status,message,country,regionName,city,isp,org,lat,lon,query"


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    # Explicitly check only RFC 1918 ranges, loopback, and link-local
    # Python 3.10+ expanded is_private to include documentation ranges which we don't want
    if addr.is_loopback or addr.is_link_local:
        return True
    if isinstance(addr, ipaddress.IPv4Address):
        return addr in ipaddress.ip_network('10.0.0.0/8') or \
               addr in ipaddress.ip_network('172.16.0.0/12') or \
               addr in ipaddress.ip_network('192.168.0.0/16')
    if isinstance(addr, ipaddress.IPv6Address):
        return addr in ipaddress.ip_network('fc00::/7')  # IPv6 private
    return False


def _geo_dict(row: IpGeoCache) -> dict:
    return {
        "is_private": bool(row.is_private),
        "country": row.country,
        "region": row.region,
        "city": row.city,
        "isp": row.isp,
        "org": row.org,
        "lat": row.lat,
        "lon": row.lon,
    }


async def lookup_ip(ip: str, session: Session) -> dict:
    cached = session.get(IpGeoCache, ip)
    if cached is not None:
        return _geo_dict(cached)

    if _is_private_ip(ip):
        row = IpGeoCache(ip=ip, is_private=1, looked_up_at=datetime.utcnow())
        session.add(row)
        session.commit()
        return _geo_dict(row)

    country = region = city = isp = org = None
    lat = lon = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(GEO_API_URL.format(ip=ip), params={"fields": GEO_API_FIELDS})
            r.raise_for_status()
            data = r.json()
        if data.get("status") == "success":
            country = data.get("country")
            region = data.get("regionName")
            city = data.get("city")
            isp = data.get("isp")
            org = data.get("org")
            lat = data.get("lat")
            lon = data.get("lon")
    except Exception:
        pass

    row = IpGeoCache(
        ip=ip, country=country, region=region, city=city, isp=isp, org=org,
        lat=lat, lon=lon, is_private=0, looked_up_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    return _geo_dict(row)
