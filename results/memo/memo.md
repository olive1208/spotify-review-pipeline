# Recommendation

Put the next quarter's product effort into **usability**, specifically ads frequency and shuffle, skip and rewind controls (ISS-USABILITY). It has the highest priority score, 28,773 [C-ISS-USABILITY-score], from 12,071 complaints [C-ISS-USABILITY-count] at a mean severity of 2.383647 [C-ISS-USABILITY-mean]. It is the largest issue with a specific, nameable cause. The only larger complaint count belongs to ISS-OTHER, which is generic dissatisfaction and gives the team nothing to act on.

## Supporting evidence

- The evidence pack describes ISS-USABILITY as frequent ads, including long runs in a row, plus limited controls: shuffle playing the wrong songs, restricted skipping and rewinding, and queue problems.
- Representative reviews: 3155da94-4c5d-446c-935c-a64d0c969096 ("Ads, ads and more ads"), e57b91ba-140f-455d-a4fb-b6d6bf8b7dae ("Can't even skip music more than 6 times and can't rewind"), and 4d341970-f786-4d9e-9d5a-74a645188ec9 (shuffle plays songs not on the playlist).
- Overall summary table: 40,182 reviews were classified as complaints and 4,550 as cancellation intent. These are expressed intent, not observed churn.

## Alternatives considered

- **Billing/paywall (ISS-BILLING):** 7,147 complaints [C-ISS-BILLING-count], severity sum 18,542 [C-ISS-BILLING-sevsum], mean severity 2.594375 [C-ISS-BILLING-mean]. It ranks third. Its reviews (e.g., 558099c8-fae1-40d8-b2a4-4abfe0e7d039) overlap with usability, since both involve skip and shuffle limits. Treat it as a related commercial question, not a separate fix. Changes here are pricing and packaging decisions, not only product work.
- **Playback (ISS-PLAYBACK):** 5,577 complaints [C-ISS-PLAYBACK-count], priority score 17,134 [C-ISS-PLAYBACK-score], mean severity 3.072261 [C-ISS-PLAYBACK-mean]. Reviews describe crashes and mid-song pauses (e.g., c5d19276-d1c6-43a0-a1a8-50e4ce2dba38). Severity per complaint is higher than usability, but volume is less than half, so it ranks fourth overall.
- **Access (ISS-ACCESS):** 1,718 complaints [C-ISS-ACCESS-count], the highest mean severity at 3.845751 [C-ISS-ACCESS-mean], but a priority score of only 6,607 [C-ISS-ACCESS-score]. Login and OTP failures (e.g., 75079d0e-e5bf-44cf-92c2-85f649781b34) are severe for affected users, so a small targeted fix is worth considering alongside the main effort.
- **Support (ISS-SUPPORT):** 128 complaints [C-ISS-SUPPORT-count], score 337 [C-ISS-SUPPORT-score]. This is too small to prioritize.

## Limitations

- The run classified a seeded random sample of 100,000 reviews out of 660,622 source rows (overall summary and scope tables), not the whole corpus.
- App reviews are self-selected and skew toward unhappy users, so counts do not represent all users.
- Severity is a model-assigned label, and 19,980 reviews are flagged as needing review (overall summary).
- ISS-OTHER's score of 27,528 [C-ISS-OTHER-score] is the second highest, but it names no specific cause.
- Usability and billing complaints overlap in the quotes, so their boundary is fuzzy.

## Next steps

1. Break ISS-USABILITY into sub-themes (ad load, skip limits, shuffle behavior, queue) to size each one.
2. Review the usability/billing overlap with commercial owners to decide which limits are intended product policy.
3. Scope a small fix for login and OTP failures (ISS-ACCESS) given its high severity.
4. Have people check a sample of the needs-review items and ISS-OTHER reviews before committing to the roadmap.