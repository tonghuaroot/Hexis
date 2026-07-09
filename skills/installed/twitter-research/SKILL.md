---
name: twitter-research
description: Search and analyze Twitter/X posts for trends, sentiment, and topic research
category: research
requires:
  tools: [twitter_search]
contexts: [heartbeat, chat]
bound_tools: [twitter_search, recall, remember]
---

# Twitter/X Research

Search Twitter/X for posts related to a topic, person, or trend. Analyze sentiment, surface key voices, and extract insights for goals that depend on social signal awareness.

## When to Use

- When the user asks "what's the buzz on [topic]" or "what are people saying about [thing]"
- When a goal involves monitoring public sentiment around a brand, product, or event
- When researching a topic where real-time public discourse adds value beyond traditional web search
- During heartbeats when an active goal has a social monitoring component

## Step-by-Step Methodology

1. **Define the query**: Translate the user's intent into a focused search query. Twitter search supports keywords, hashtags, mentions (@user), and boolean operators. Be specific -- broad queries return noise.
2. **Set scope**: Determine whether the user wants recent posts (last 24-48 hours) or a broader time window. Default to recent unless otherwise specified. Consider filtering by minimum engagement (likes, retweets) to surface signal over noise.
3. **Execute search**: Call `twitter_search` with the crafted query. Review the returned posts for relevance before analysis.
4. **Analyze themes**: Group the results by recurring themes or talking points. Identify:
   - **Dominant sentiment**: Is the conversation positive, negative, mixed, or neutral?
   - **Key voices**: Are there notable accounts (high follower count, verified, industry figures) driving the conversation?
   - **Emerging narratives**: What arguments or framings are gaining traction?
5. **Extract quotes**: Pull 2-3 representative posts that best illustrate the main themes. Include the author handle and engagement metrics for context.
6. **Cross-reference**: Use `recall` to check if this topic has been researched before. Note how sentiment or narratives have shifted since the last check.
7. **Store findings**: If the research is tied to an ongoing goal, use `remember` to persist the key findings as a semantic memory with the date, query, and summary.

## Quality Guidelines

- Twitter data is noisy and skewed. Never present a handful of posts as representative of broad public opinion without noting the limitation.
- Attribute quotes to their authors. Do not paraphrase a tweet without noting the source.
- Be cautious with sentiment analysis. Sarcasm, irony, and quote-tweeting for disagreement are common and easily misread.
- Respect rate limits on the Twitter API. Do not run the same search repeatedly in a short window.
- When reporting results, distinguish between high-engagement posts (broad reach) and low-engagement posts (niche signal). Both are useful but mean different things.
- In heartbeat context, only run Twitter research when an active goal explicitly requires social monitoring. It is not a default heartbeat activity.
