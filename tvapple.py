import json
import os
import re
# Folder this script lives in
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INPUT_1 = os.path.join(BASE_DIR, "monarch_1.json")   # episode-detail dump (Season 1, complete)
INPUT_2 = os.path.join(BASE_DIR, "monarch_2.json")   # show-page dump (series info + partial episode window)
INPUT_3 = os.path.join(BASE_DIR, "monarch_3.json")   # extended episode dump (Season 2 episodes 7-10)
OUTPUT = os.path.join(BASE_DIR, "monarch_details_extract.json")


def safe_get(obj, keys, default=None):
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key, default)
        elif isinstance(obj, list) and isinstance(key, int):
            try:
                obj = obj[key]
            except IndexError:
                return default
        else:
            return default
        if obj is default and key != keys[-1]:
            return default
    return obj


def format_image_url(template, width=None, height=None, fmt="jpg"):
    if not template:
        return "Na"
    w = width or 3840
    h = height or 2160
    return (
        template.replace("{w}", str(w))
        .replace("{h}", str(h))
        .replace("{f}", fmt)
    )


def parse_tag_number(tag):
    if not tag:
        return None
    m = re.search(r"\d+", str(tag))
    return int(m.group(0)) if m else None


def seconds_to_minutes_label(seconds):
    if seconds is None:
        return "Na"
    if seconds > 10000:  # guard against ms values
        seconds = seconds / 1000.0
    return f"{int(round(seconds / 60))} min"


def epoch_ms_to_date(epoch_ms):
    if epoch_ms is None:
        return "Na"
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_language_string(raw):
    if not raw:
        return []
    compounds = ["Chinese, Simplified", "Chinese, Traditional", "Cantonese, Traditional"]
    placeholder_map = {}
    protected = raw
    for i, c in enumerate(compounds):
        token = f"\x00{i}\x00"
        placeholder_map[token] = c
        protected = protected.replace(c, token)

    parts = re.split(r",\s*(?![^(]*\))", protected)
    cleaned = []
    for part in parts:
        name = part.strip()
        for token, original in placeholder_map.items():
            name = name.replace(token, original)
        name = re.sub(r"\s*\((?:AD|CC|SDH)[^)]*\)\s*$", "", name).strip()
        if name and name not in cleaned:
            cleaned.append(name)
    return cleaned


def main():
    with open(INPUT_1, "r", encoding="utf-8") as f:
        file1 = json.load(f)
    with open(INPUT_2, "r", encoding="utf-8") as f:
        file2 = json.load(f)
    with open(INPUT_3, "r", encoding="utf-8") as f:
        file3 = json.load(f)

    show_intent = file2["data"][1]["data"]
    shelves = show_intent["shelves"]

    shelves_by_header_title = {}
    for s in shelves:
        t = safe_get(s, ["header", "title"])
        if t:
            shelves_by_header_title[t] = s

    # ---------- Series-level hero info ----------
    hero = shelves[0]["items"][0]
    series_id = safe_get(hero, ["buttons", 0, "action", "actionMetrics", "data", 0, "fields", "canonicalId"])
    title = hero.get("title")
    synopsis = hero.get("description")
    series_url = show_intent.get("canonicalURL")

    # ---------- About shelf (genres) ----------
    about_shelf = next((s for s in shelves if s.get("$type") == "About"), None)
    genres = []
    if about_shelf:
        genres = safe_get(about_shelf, ["items", 0, "genres"], [])

    # ---------- Info shelf ----------
    info_shelf = next((s for s in shelves if s.get("$type") == "Info"), None)
    release_year = None
    content_rating = None
    content_advisory = []
    subtitles = []
    if info_shelf:
        groups = {g.get("title"): g for g in info_shelf.get("items", [])}
        info_group = groups.get("Information", {}).get("items", [])
        for entry in info_group:
            if entry.get("id") == "information-releaseDate":
                m = re.search(r"\d{4}", entry.get("info", ""))
                if m:
                    release_year = int(m.group(0))
            elif entry.get("id") == "information-rating":
                content_rating = entry.get("info")
            elif entry.get("id") == "information-contentRatingAdvisories":
                content_advisory = [a.strip() for a in entry.get("info", "").split(",") if a.strip()]

        lang_group = groups.get("Languages", {}).get("items", [])
        for entry in lang_group:
            if entry.get("id") == "languages-subtitles":
                subtitles = parse_language_string(entry.get("info", ""))

    # Audio languages
    playables = file1.get("data", {}).get("playables", {})
    original_lang = None
    audio_set = []
    if playables:
        first_playable = next(iter(playables.values()))
        audio_set = [t.get("displayName") for t in first_playable.get("audioTrackLocales", []) if t.get("displayName")]
    first_ep = safe_get(file1, ["data", "episodes", 0], {})
    original_langs = first_ep.get("originalSpokenLanguages", [])
    if original_langs:
        original_lang = original_langs[0].get("displayName")
    audio_languages = ([original_lang] if original_lang else []) + [l for l in audio_set if l != original_lang]

    # ---------- Cast & Crew ----------
    cast_shelf = shelves_by_header_title.get("Cast & Crew")
    producers = []
    cast = []
    if cast_shelf:
        for item in cast_shelf.get("items", []):
            name = item.get("title")
            role = item.get("subtitle", "")
            if not name:
                continue
            if role == "Executive Producer":
                producers.append(name)
            else:
                cast.append(name)

    # ---------- Trailers & Bonus ----------
    all_trailers_and_bonus = []
    for shelf_name in ("Trailers", "Bonus Content"):
        shelf = shelves_by_header_title.get(shelf_name)
        if not shelf:
            continue
        for item in shelf.get("items", []):
            artwork = item.get("artwork", {})
            all_trailers_and_bonus.append({
                "title": safe_get(item, ["contextAction", "title"]) or item.get("title"),
                "video_stream_url": safe_get(item, ["contextAction", "url"]),
                "thumbnail_url": format_image_url(artwork.get("template"), artwork.get("width"), artwork.get("height")),
                "content_rating": None,
                "duration": item.get("metadata"),
            })

    trailers_section = all_trailers_and_bonus[:4]
    bonus_section = all_trailers_and_bonus[4:7]

    # ---------- Season 1 Episodes ----------
    season1_episodes = []
    file1_episodes = file1.get("data", {}).get("episodes", [])
    file1_titles = set()
    for ep in file1_episodes:
        images = ep.get("images", {}).get("contentImage", {})
        season1_episodes.append({
            "episode_number": ep.get("episodeNumber"),
            "episode_title": ep.get("title"),
            "episode_url": ep.get("url"),
            "thumbnail_url": format_image_url(images.get("url"), images.get("width"), images.get("height")),
            "synopsis": ep.get("description"),
            "content_rating": safe_get(ep, ["rating", "displayName"], "Na"),
            "duration": seconds_to_minutes_label(ep.get("duration")),
            "release_date": epoch_ms_to_date(ep.get("releaseDate")),
        })
        file1_titles.add(ep.get("title", "").strip().lower())

    season1_episodes.sort(key=lambda e: e["episode_number"])

    # ---------- Season 2 Episodes ----------
    season2_episodes = []
    episodes_shelf = shelves_by_header_title.get("Episodes")
    
    # 1. Parse initial Season 2 episodes found in monarch_2.json
    if episodes_shelf:
        for item in episodes_shelf.get("items", []):
            if item.get("type") != "Episode":
                continue
            ep_title = item.get("title")
            if not ep_title or ep_title.strip().lower() in file1_titles:
                continue
            
            artwork = item.get("artwork", {})
            season2_episodes.append({
                "episode_number": parse_tag_number(item.get("tag")),
                "episode_title": ep_title,
                "episode_url": safe_get(item, ["contextAction", "url"]),
                "thumbnail_url": format_image_url(artwork.get("template"), artwork.get("width"), artwork.get("height")),
                "synopsis": item.get("description"),
                "content_rating": "Na",
                "duration": item.get("metadata"),
                "release_date": "Na",
            })

    # Get official total episode count from metadata
    seasons_meta = {s["seasonNumber"]: s for s in safe_get(episodes_shelf, ["header", "seasons"], [])} if episodes_shelf else {}
    official_count = seasons_meta.get(2, {}).get("episodeCount", 10)

    # 2. Map all episodes from monarch_3.json by episodeNumber
    file3_episodes = file3.get("data", {}).get("episodes", [])
    file3_episodes_by_num = {}
    for ep in file3_episodes:
        num = ep.get("episodeNumber")
        if num is not None:
            file3_episodes_by_num[int(num)] = ep

    # 3. Fill missing entries or overwrite incomplete data using monarch_3.json
    existing_ep_numbers = {ep["episode_number"] for ep in season2_episodes if ep["episode_number"] is not None}

    for ep_num in range(1, official_count + 1):
        if ep_num not in existing_ep_numbers:
            if ep_num in file3_episodes_by_num:
                ep_data = file3_episodes_by_num[ep_num]
                images = ep_data.get("images", {}).get("contentImage", {})
                
                season2_episodes.append({
                    "episode_number": int(ep_data.get("episodeNumber")),
                    "episode_title": ep_data.get("title") or "Na",
                    "episode_url": ep_data.get("url") or "Na",
                    "thumbnail_url": format_image_url(images.get("url"), images.get("width"), images.get("height")),
                    "synopsis": ep_data.get("description") or "Na",
                    "content_rating": safe_get(ep_data, ["rating", "displayName"], "Na"),
                    "duration": seconds_to_minutes_label(ep_data.get("duration")),
                    "release_date": epoch_ms_to_date(ep_data.get("releaseDate")) or "Na",
                })
            else:
                season2_episodes.append({
                    "episode_number": ep_num,
                    "episode_title": "Na",
                    "episode_url": "Na",
                    "thumbnail_url": "Na",
                    "synopsis": "Na",
                    "content_rating": "Na",
                    "duration": "Na",
                    "release_date": "Na",
                })
            
    # Linear sort to guarantee order from 1 to 10
    season2_episodes.sort(key=lambda e: (e["episode_number"] is None, e["episode_number"]))

    seasons_output = []
    if season1_episodes:
        seasons_output.append({
            "season_label": seasons_meta.get(1, {}).get("title", "Season 1"),
            "total_episodes_count": len(season1_episodes),
            "episodes": season1_episodes,
        })
    if season2_episodes:
        seasons_output.append({
            "season_label": seasons_meta.get(2, {}).get("title", "Season 2"),
            "total_episodes_count": len(season2_episodes),
            "total_episodes_count_official": official_count,
            "episodes": season2_episodes,
        })

    total_seasons_count = f"{len(seasons_output)} Season" + ("" if len(seasons_output) == 1 else "s")

    result = {
        "series_id": series_id,
        "series_url": series_url,
        "title": title,
        "is_new_series": False,
        "ranking": None,
        "synopsis": synopsis,
        "genres": genres,
        "imdb_rating": None,
        "release_year": release_year,
        "total_seasons_count": total_seasons_count,
        "content_advisory": content_advisory,
        "audio_languages": audio_languages,
        "subtitles": subtitles,
        "creators_and_cast": {
            "directors": [],
            "producers": producers,
            "cast": cast,
            "studio": None,
        },
        "trailers": trailers_section,
        "bonus_content": bonus_section,
        "seasons": seasons_output,
    }

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()