# Golden-50 evaluation

```json
{
  "cases": 50,
  "valid_predictions": 50,
  "missing_or_quarantined_count_as_wrong": 0,
  "topic_agreement": 0.86,
  "intent_agreement": 0.88,
  "severity_exact_agreement": 0.82,
  "severity_mae": 0.2,
  "sentiment_mae": 0.25,
  "sentiment_within_tolerance": 0.88,
  "sentiment_tolerance": 0.5,
  "topic_intent_severity_all_agree": 0.68,
  "quote_exact_substring_rate": 1.0,
  "human_ambiguous_cases(needs_review=true)": 3,
  "needs_review_confusion(human,model)": {
    "False,True": 10,
    "False,False": 37,
    "True,False": 3
  },
  "entity_strings_not_literally_in_text": 15,
  "human_topic_counts": {
    "catalog": 6,
    "other": 27,
    "usability": 8,
    "billing": 4,
    "playback": 4,
    "downloads": 1
  },
  "model_topic_counts": {
    "other": 26,
    "catalog": 7,
    "playback": 5,
    "billing": 5,
    "usability": 7
  },
  "run": "runs/full",
  "label_config": "claude-haiku-5-5+enrich_v1-a2cff36f+schema_v1+b50",
  "note": "50 cases are a small diagnostic sample, not a precise accuracy estimate."
}
```

**Topic confusion** (rows = human, columns = model)

| human \ model | usability | playback | downloads | catalog | billing | other |
|---|---|---|---|---|---|---|
| usability | 5 | 1 |  | 1 | 1 |  |
| playback |  | 4 |  |  |  |  |
| downloads |  |  |  |  | 1 |  |
| catalog |  |  |  | 6 |  |  |
| billing | 2 |  |  |  | 2 |  |
| other |  |  |  |  | 1 | 26 |

**Intent confusion** (rows = human, columns = model)

| human \ model | cancellation | complaint | request | praise | unclear |
|---|---|---|---|---|---|
| cancellation | 1 |  |  |  | 1 |
| complaint | 1 | 19 | 1 |  |  |
| request |  |  | 1 |  |  |
| praise |  |  |  | 21 |  |
| unclear |  | 1 | 1 | 1 | 2 |

## Disagreements (topic, intent or severity)

| review_id | text | human t/i/s | model t/i/s | cache |
|---|---|---|---|---|
| 292ce26a | Staff must be full of mentally ill kool-aid heads. Banning conservative music that speaks  | catalog/complaint/4 | catalog/complaint/2 |  |
| 991b6b3a | Ads were okay, but limited functionality..it's just too much | usability/complaint/3 | billing/complaint/3 |  |
| 1fc8b08f | Duo Premium .... No ads at all! | billing/praise/1 | usability/praise/1 |  |
| 46842184 | asem, lirik nya kdang gaada kek mana?? | catalog/complaint/3 | catalog/complaint/2 |  |
| 5de7f95b | We are boycotting Swedish app in our protest against Sweden for their desrecpect & descera | other/cancellation/2 | other/unclear/1 |  |
| 372d4e67 | Please ye ads ko thoda kumm Karo harr ek song ke baad ad 🙏🙄 | usability/complaint/2 | usability/request/2 |  |
| 47f1406d | Very poor updates as we cannot playback the songs and most bad updates are happening it is | playback/complaint/4 | playback/complaint/3 |  |
| f4c8146a | Unconditional love, Stoli Canales | other/unclear/1 | other/praise/1 |  |
| 3ddb3f4e | Deleting this App. The new update suck, we can't choose our fvrt songs after some clicks,  | billing/cancellation/4 | usability/cancellation/3 |  |
| 8cc4fad4 | I hate this app and sweden .I love islam | other/unclear/1 | other/complaint/2 | cd9620ce |
| 283b5843 | Beth, we all got to give me a free dollar to go with 3 months, bro I can't be like | other/unclear/1 | billing/request/1 |  |
| 8c0546b0 | 1-Lyrics not getting loaded, 2-can't go back to or start song where to wanted. Pathetic up | catalog/complaint/4 | catalog/complaint/3 |  |
| b58dd6f0 | The only thing Spotify lacks is the ability to organize saved  albums/artists into collect | usability/request/1 | catalog/request/1 |  |
| dceb14e7 | I gave one star because I can't give any less than that. the new update is sooooooo annoyi | usability/complaint/4 | playback/complaint/3 |  |
| ac4e860a | paying for subscribing but unable to play in offline mode really? | downloads/complaint/4 | billing/complaint/3 |  |
| d2f3874f | Too expensive and the free version is pretty much unusable with constant ads... Every two  | billing/complaint/2 | billing/cancellation/2 |  |

## Entities not literally present in the text (inspect: translation/normalization vs. invented)

- 292ce26a: `recommendations` in "Staff must be full of mentally ill kool-aid heads. Banning conservative music that speaks truth. Spotify is doing all th"
- 46842184: `lyrics` in "asem, lirik nya kdang gaada kek mana??"
- 3ddb3f4e: `queue` in "Deleting this App. The new update suck, we can't choose our fvrt songs after some clicks, we can't CHOOSE song after a s"
- 215463ae: `update` in "What is happening with Spotify?? Recently I'm trying to play a song but it doesn't working. So I unstalled and thn again"
- 215463ae: `login` in "What is happening with Spotify?? Recently I'm trying to play a song but it doesn't working. So I unstalled and thn again"
- 46c0b49f: `ads` in "Are you out of your mind? This is dangerous! Listening to music in a normal volume and suddenly an add starts and my ear"
- 283b5843: `premium` in "Beth, we all got to give me a free dollar to go with 3 months, bro I can't be like"
- 2aa566c6: `ads` in "Nice app, very less advertising"
- b58dd6f0: `playlist` in "The only thing Spotify lacks is the ability to organize saved 
albums/artists into collections. Would definitely deserve"
- dceb14e7: `repeat` in "I gave one star because I can't give any less than that. the new update is sooooooo annoying like I can't even play the "
- 5fba12ea: `recommendations` in "I find Spotify very good, it finds and play whatever music you want and also adds songs that you might like, judging fro"
- ac4e860a: `premium` in "paying for subscribing but unable to play in offline mode really?"
- 9034d675: `ads` in "Worst app ever too much ad"
- d2f3874f: `price` in "Too expensive and the free version is pretty much unusable with constant ads... Every two or three songs.. Forget it"
- d2f3874f: `premium` in "Too expensive and the free version is pretty much unusable with constant ads... Every two or three songs.. Forget it"
