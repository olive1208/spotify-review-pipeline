# Recommendation

Put the next quarter's product effort into **usability (ISS-USABILITY: ads, skip limits, and UX changes)**. It ranks first with a priority score of 34 [C-ISS-USABILITY-score], from 15 complaints [C-ISS-USABILITY-count] and a severity sum of 34 [C-ISS-USABILITY-sevsum]. The mean severity is moderate at 2.266667 [C-ISS-USABILITY-mean], so this is a case of volume rather than acute pain. Playback crashes are a close second-tier concern and should be handled as a targeted fix alongside it.

## Supporting evidence

Per the overall summary table, the run classified 100 reviews, of which 43 were complaints and 8 expressed cancellation intent. Usability complaints centre on ad frequency and on skip, back, and repeat limits for non-subscribers. Representative reviews:

- 3e8bdc92-4934-457e-85cc-8b9d14bf51c0: "it's always every 1 or 2 song then instant ads"
- a3605554-5c3a-441d-836e-e9fdea83eb2e: "The ability to skip a song or go to previous song has been removed from non subscribed users."
- 94c9f2f9-36fa-4c11-936e-660514fe7a35: "the new homescreen update is horrible"

## Alternatives considered

- **Playback (ISS-PLAYBACK):** 6 complaints [C-ISS-PLAYBACK-count], a priority score of 19 [C-ISS-PLAYBACK-score], and a higher mean severity of 3.166667 [C-ISS-PLAYBACK-mean]. These are crashes and loading hangs after an update, for example 55cc3abf-c295-4591-8c3a-78085cfdefaa and 66a29b3c-c14d-440e-b722-b23e0c99ae82. Each report is more severe, but there are fewer of them, so it ranks lower. It is a reasonable candidate for a focused stability fix.
- **Billing (ISS-BILLING):** 5 complaints [C-ISS-BILLING-count], a priority score of 15 [C-ISS-BILLING-score], and a mean severity of 3.000000 [C-ISS-BILLING-mean]. The reviews, such as d146397f-4a94-4980-be00-ae9212eca3a7, are about basic features sitting behind Premium, not payment errors. The theme resembles the skip-limit complaints in usability, so the two may be addressed together.
- **Access (ISS-ACCESS):** 2 complaints [C-ISS-ACCESS-count] but the highest mean severity, 4.000000 [C-ISS-ACCESS-mean], and a priority score of 8 [C-ISS-ACCESS-score]. Login failures (de0a9cdf-8974-48bc-802f-331228df4619) are serious, but the count is too small to lead the quarter.
- **Support (ISS-SUPPORT):** 1 complaint [C-ISS-SUPPORT-count] and a priority score of 2 [C-ISS-SUPPORT-score]. This is the lowest priority.

ISS-OTHER (general dissatisfaction after an update) ranks second at 28 [C-ISS-OTHER-score], but it is a catch-all without a specific problem to act on, so I did not treat it as a candidate area.

## Limitations

- The run covered only 100 reviews, so the counts are small and rankings could shift with a larger sample.
- App reviews are self-selected and skew toward unhappy users.
- 14 reviews were flagged as needing review, so some classifications may change.
- Cancellation language is expressed intent, not observed churn.
- The data does not tell us why users are unhappy beyond what they quote.

## Next steps

1. Review the ads, skip-limit, and homescreen complaints with design and monetization owners to separate what is a deliberate product choice from what is a UX regression.
2. Assign a small stability team to the update-related crashes and loading hangs in ISS-PLAYBACK.
3. Re-run the classification on a larger review sample to confirm the ranking before committing the full quarter.
4. Break down the ISS-OTHER reviews to see whether they belong under usability or playback.