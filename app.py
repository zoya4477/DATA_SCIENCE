# app.py
import os
from datetime import datetime
from dateutil import parser
from googleapiclient.discovery import build
import pandas as pd
import streamlit as st
import plotly.express as px
from dotenv import load_dotenv

load_dotenv()  # optional: load YT_API_KEY from .env

st.set_page_config(page_title="YouTube Data Dashboard", layout="wide")

st.title("YouTube Data Dashboard")

# --- CONFIG ---
API_KEY = os.getenv("AIzaSyBvGblsGP8ry0n_D3fvMT_jGtlAgkI9KPs") or st.secrets.get("YOUTUBE_API_KEY") if "YOUTUBE_API_KEY" in st.secrets else None

if not API_KEY:
    st.sidebar.error("Add your YouTube API key to environment variable YOUTUBE_API_KEY or Streamlit secrets.")

channel_identifier = st.sidebar.text_input("Enter channel ID or username (for username, prepend 'user:' e.g. user:PewDiePie)", value="")
max_results = st.sidebar.slider("Max videos to fetch", 10, 200, 50)

# choose method: channel id or username
fetch_button = st.sidebar.button("Fetch channel & videos")

@st.cache_data(ttl=300)
def build_youtube(api_key):
    return build("youtube", "v3", developerKey=api_key)

def resolve_channel_id(youtube, identifier):
    # identifier could be channel ID, or 'user:username', or a plain username
    if identifier.startswith("UC"):  # likely a channel id
        return identifier
    if identifier.startswith("user:"):
        username = identifier.split("user:",1)[1]
        res = youtube.channels().list(part="id", forUsername=username).execute()
        items = res.get("items", [])
        if items:
            return items[0]["id"]
    # fallback: try search by channel name
    res = youtube.search().list(part="snippet", q=identifier, type="channel", maxResults=1).execute()
    items = res.get("items", [])
    if items:
        return items[0]["snippet"]["channelId"]
    return None

def fetch_channel_info(youtube, channel_id):
    resp = youtube.channels().list(part="snippet,statistics", id=channel_id).execute()
    items = resp.get("items", [])
    if not items:
        return None
    item = items[0]
    data = {
        "channelId": item["id"],
        "title": item["snippet"].get("title"),
        "description": item["snippet"].get("description"),
        "publishedAt": item["snippet"].get("publishedAt"),
        "viewCount": int(item["statistics"].get("viewCount", 0)),
        "subscriberCount": int(item["statistics"].get("subscriberCount", 0)) if item["statistics"].get("hiddenSubscriberCount", False) == False else None,
        "videoCount": int(item["statistics"].get("videoCount", 0))
    }
    return data

def fetch_videos_for_channel(youtube, channel_id, max_results=50):
    # get uploads playlist id from channel's contentDetails
    ch = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    items = ch.get("items", [])
    uploads_pl = None
    if items:
        uploads_pl = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    videos = []
    nextPageToken = None
    fetched = 0
    while True:
        res = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_pl,
            maxResults=min(50, max_results - fetched),
            pageToken=nextPageToken
        ).execute()
        for it in res.get("items", []):
            sn = it["snippet"]
            vid_id = sn["resourceId"]["videoId"]
            videos.append({
                "videoId": vid_id,
                "title": sn.get("title"),
                "publishedAt": sn.get("publishedAt")
            })
            fetched += 1
        nextPageToken = res.get("nextPageToken")
        if not nextPageToken or fetched >= max_results:
            break

    # fetch stats in batches
    rows = []
    for i in range(0, len(videos), 50):
        batch = videos[i:i+50]
        ids = ",".join([v["videoId"] for v in batch])
        stats_res = youtube.videos().list(part="statistics,contentDetails,snippet", id=ids).execute()
        for vd in stats_res.get("items", []):
            sid = vd["id"]
            snippet = vd.get("snippet", {})
            stats = vd.get("statistics", {})
            rows.append({
                "videoId": sid,
                "title": snippet.get("title"),
                "publishedAt": snippet.get("publishedAt"),
                "viewCount": int(stats.get("viewCount", 0)),
                "likeCount": int(stats.get("likeCount", 0)) if "likeCount" in stats else None,
                "commentCount": int(stats.get("commentCount", 0)) if "commentCount" in stats else None,
                "duration": vd.get("contentDetails", {}).get("duration")
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["publishedAt"] = pd.to_datetime(df["publishedAt"])
    return df

if fetch_button and channel_identifier and API_KEY:
    youtube = build_youtube(API_KEY)
    with st.spinner("Resolving channel ID..."):
        channel_id = resolve_channel_id(youtube, channel_identifier)
    if not channel_id:
        st.error("Could not find the channel. Try channel ID or 'user:username' or exact channel name.")
    else:
        st.success(f"Channel ID: {channel_id}")
        ch_info = fetch_channel_info(youtube, channel_id)
        if ch_info:
            st.markdown("### Channel summary")
            col1, col2, col3 = st.columns(3)
            col1.metric("Subscribers", ch_info["subscriberCount"] if ch_info["subscriberCount"] is not None else "Hidden")
            col2.metric("Total views", f'{ch_info["viewCount"]:,}')
            col3.metric("Total videos", ch_info["videoCount"])
            st.write(ch_info["description"][:500] + ("..." if len(ch_info["description"])>500 else ""))
        else:
            st.warning("No channel info returned.")

        with st.spinner("Fetching videos and stats (this can take a few seconds)..."):
            df_videos = fetch_videos_for_channel(youtube, channel_id, max_results=max_results)

        if df_videos.empty:
            st.info("No videos found.")
        else:
            st.markdown("### Videos dataframe")
            st.dataframe(df_videos.sort_values("viewCount", ascending=False).reset_index(drop=True))

            st.markdown("### Filters")
            min_views = int(st.slider("Minimum views", 0, int(df_videos["viewCount"].max()), 0))
            date_from = st.date_input("Published after", value=df_videos["publishedAt"].min().date())
            date_to = st.date_input("Published before", value=df_videos["publishedAt"].max().date())

            filtered = df_videos[
                (df_videos["viewCount"] >= min_views) &
                (df_videos["publishedAt"].dt.date >= date_from) &
                (df_videos["publishedAt"].dt.date <= date_to)
            ]

            st.markdown("### Top performing videos (by views)")
            top_n = st.slider("Top N", 3, 20, 5)
            top_videos = filtered.sort_values("viewCount", ascending=False).head(top_n)
            fig_bar = px.bar(top_videos, x="title", y="viewCount", hover_data=["likeCount","commentCount"], title=f"Top {top_n} videos by views")
            st.plotly_chart(fig_bar, use_container_width=True)

            st.markdown("### Subscriber / Views trend (per video publish date)")
            # aggregate views by publish date (monthly)
            df_ts = df_videos.copy()
            df_ts["month"] = df_ts["publishedAt"].dt.to_period("M").dt.to_timestamp()
            monthly = df_ts.groupby("month", as_index=False)["viewCount"].sum().sort_values("month")
            fig_line = px.line(monthly, x="month", y="viewCount", title="Monthly views (sum of video views)")
            st.plotly_chart(fig_line, use_container_width=True)

            st.markdown("### Individual video detail")
            sel_vid = st.selectbox("Pick a video", options=top_videos["videoId"].tolist(), format_func=lambda vid: top_videos[top_videos["videoId"]==vid]["title"].values[0])
            vid_row = df_videos[df_videos["videoId"]==sel_vid].iloc[0]
            st.write("Title:", vid_row["title"])
            st.write("Published:", vid_row["publishedAt"])
            st.write(f"Views: {vid_row['viewCount']:,}")
            st.write(f"Likes: {vid_row['likeCount']:,}" if pd.notna(vid_row['likeCount']) else "Likes: N/A")
            st.write(f"Comments: {vid_row['commentCount']:,}" if pd.notna(vid_row['commentCount']) else "Comments: N/A")
            st.video(f"https://www.youtube.com/watch?v={sel_vid}")

