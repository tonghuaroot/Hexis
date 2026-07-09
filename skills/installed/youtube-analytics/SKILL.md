---
name: youtube-analytics
description: Retrieve and analyze YouTube channel stats, video performance, and content trends
category: analytics
requires:
  tools: [youtube_channel_stats]
  env: [YOUTUBE_API_KEY]
contexts: [heartbeat, chat]
bound_tools: [youtube_channel_stats, youtube_search, youtube_video_stats, recall, remember]
---

# YouTube Analytics

Pull channel-level statistics, search for videos by topic, and analyze content performance trends on YouTube.

## When to Use

- When the user asks about a YouTube channel's performance ("how is my channel doing", "show me stats for [channel]")
- When researching a topic where video content is a primary source (tutorials, reviews, news commentary)
- When a goal involves content strategy and competitive analysis on YouTube
- During heartbeats when an active goal tracks channel growth or content performance

## Step-by-Step Methodology

1. **Identify the target**: Determine which channel or topic the user is interested in. For their own channel, use the configured channel ID. For competitor or topic research, use `search_youtube_videos` to find relevant channels.
2. **Pull channel stats**: Use `youtube_channel_stats` to retrieve subscriber count, total views, video count, and recent upload frequency. These are the headline metrics.
3. **Analyze recent uploads**: Look at the last 10-20 videos for performance patterns:
   - **View velocity**: How quickly do new videos accumulate views in the first 48 hours?
   - **Engagement ratio**: Likes and comments relative to views. Higher ratios suggest stronger audience connection.
   - **Title and topic patterns**: Which topics or formats perform above the channel average?
4. **Benchmark against history**: If previous analytics memories exist (from earlier runs of this skill), compare current stats to historical baselines. Note growth rate, engagement trends, and any inflection points.
5. **Search for topic trends**: Use `search_youtube_videos` with relevant keywords to see what content is performing well in the broader niche. Identify gaps or opportunities.
6. **Compile the report**: Structure the output as:
   - **Channel overview**: Headline stats (subscribers, views, videos)
   - **Recent performance**: Last 5-10 video performance summary
   - **Trends**: Growth trajectory, engagement shifts, standout content
   - **Opportunities**: Topics or formats worth exploring based on search data
7. **Store for tracking**: Use `remember` to persist the analytics snapshot as a semantic memory, enabling trend comparison on future runs.

## Quality Guidelines

- Present numbers in context. "50,000 views" means different things for a 1K-subscriber channel versus a 1M-subscriber channel. Always include relative metrics.
- Do not over-interpret short-term fluctuations. A single viral video or a slow week is not a trend. Look for patterns across 10+ data points.
- When comparing channels, ensure the comparison is fair (similar niche, similar age, similar upload frequency).
- Respect YouTube API quota limits. Cache channel stats and avoid redundant calls within the same session.
- If the API key is missing or quota is exhausted, report the limitation clearly rather than failing silently.
- In heartbeat context, only run analytics when an active goal requires YouTube monitoring. This is not a routine check.
