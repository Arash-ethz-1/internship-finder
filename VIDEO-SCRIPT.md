# Demo video script

For the walkthrough embedded at the top of [`README.md`](README.md). Save the
finished file as `docs/demo.gif` (silent, for the README) or `docs/demo.mp4`
(with the voiceover, for anywhere that plays audio).

**Runtime:** about 2:15 as written. **Format:** you on camera for the open and
the close, screen recording with voiceover in between. If you would rather not
be on camera at all, cut sections 1 and 8 and start on the grid; the script still
works, it just loses the framing.

**Before recording**

- `python dev.py`, then let the grid finish its first load so nothing is cold.
- Have one posting already at `interested` with a letter drafted, so section 5
  does not wait on the model. Draft it live only if you are happy to sit through
  it, in which case say so out loud rather than cutting the silence.
- Stage the mailbox so that clicking **check mail** on camera actually finds
  something. Full recipe in [Staging the inbox](#staging-the-inbox), and it needs
  doing a few minutes before you record, not during.
- Window at 1440x900. Hide bookmarks. Close the second monitor's notifications.
- Do not show `backend/.env` on screen at any point.

Lines marked **[you]** are spoken. Lines in *italics* are what is happening on
screen. Timestamps are targets, not gospel.

---

## 1. Open, on camera (0:00 to 0:15)

*You, talking to the camera. No screen yet.*

**[you]** I applied to internships for about a month last year, and the thing
that actually wore me down was not the writing. It was that every job board
showed me the same postings I had already decided against, and had no idea I had
decided anything.

**[you]** So I built the version I wanted. It runs on my laptop. Let me show you.

---

## 2. The corpus (0:15 to 0:35)

*Cut to `/postings`. The grid, scrolling. The footer count is visible.*

**[you]** Twenty four thousand postings, from five hundred and eighty two
companies. These come from four applicant tracking systems: Greenhouse, Lever,
Ashby and Personio. Public APIs, so nothing here is scraped.

*Open the filter rail. Set region to Europe, level to intern. The count lands on 173.*

**[you]** Every location has been resolved offline into a city, a country and a
region. That matters more than it sounds like it should, because a board will
write the same place as Zurich, or Z, u, umlaut, rich, or CH dash Zurich, or
Massachusetts dash Boston, backwards. Ninety seven point seven percent of them
resolve.

**[you]** So "internships in Europe" is a filter now. It is a hundred and
seventy three of them.

---

## 3. Asking for something (0:35 to 1:00)

*Cut to `/chat`. Type: `ml research internships in zurich`.*

**[you]** This is the agent. It is not a search box with better manners: it picks
the filters itself, runs the search, and reports what it actually found.

*The `find_postings` tool call block appears, then results stream in.*

**[you]** Under that is hybrid retrieval. Dense vectors for the meaning, BM25 for
the exact words, and the two rankings fused together.

*Hover a result so the trace bar splits into its two segments.*

**[you]** And because the two halves are scored separately, you can see which one
earned a result its place. The bar adds up to the score. It is not decoration, it
is the actual arithmetic.

---

## 4. Deciding (1:00 to 1:20)

*Mark two results "not for me". Then open a third in the detail panel and press 1.*

**[you]** Here is the part I care most about. "Not for me" is me passing on a
posting. It is a completely different fact from a company rejecting me, and most
tools store both as the same thing, which is how you end up looking at a pipeline
that claims you have thirty rejections you never actually got.

**[you]** These two are gone. This one is interested. The whole triage is
keyboard, one through five.

---

## 5. The letter (1:20 to 1:45)

*Click through to `/letters/:id`. The draft is there, or streams in.*

**[you]** The letter is grounded in my own project write-ups, retrieved from the
same index as the postings.

*Scroll so a real project name is on screen.*

**[you]** So it names real things I built. And when it does not know something,
it leaves a TODO instead of inventing it. That one is asking me for a graduation
date, because nothing in my write-ups pins it down.

*Type in the revision box: `make it about a third shorter, keep every concrete project detail`.*

**[you]** And revision is a change to this letter, not a reroll. The grounding
goes back into the prompt unchanged, so an edit cannot invent a fact to fill the
gap the edit just opened.

*The draft shortens.*

**[you]** Three eighty five words down to two forty seven, every project still
named.

---

## 6. The replies (1:45 to 2:05)

*Cut to `/inbox`. Empty, or holding whatever was already there.*

**[you]** Two weeks later the replies start arriving. So this reads my mail.

*Click **check mail**. The button goes to a running state.*

**[you]** Read only, and metadata only, so the message body is never even
fetched. It takes every company I have actually applied to, matches the incoming
mail against that list, and asks the model what each one is.

*The three staged suggestions appear: interview, rejection, and the collapsed
"not about an application" section.*

**[you]** Interview invitation, ninety eight percent. Rejection, ninety one. And
one it decided was not about an application at all, which it folds away rather
than guessing at.

*Hover the accept button. Do not click. Let the line underneath be readable.*

**[you]** And then it stops. These are suggestions. Accepting one is what moves
the application, and the history records which email did it. I would rather do
one click than have a mis-filed rejection quietly stop me from checking on a
company that wanted to talk.

---

## 7. The pipeline (2:05 to 2:10)

*Cut to `/stats`.*

**[you]** And this is the whole pipeline, stage by stage.

---

## 8. Close, on camera (2:10 to 2:15)

*Back to camera.*

**[you]** All of it local. One SQLite file, one array of vectors. The repo is
linked below, it is one command to run.

*Title card: Screener. `python dev.py`. Fade.*

---

## Staging the inbox

Section 6 only works if **check mail** finds something on camera. Two things in
the code decide whether it will:

* **The matcher only ever considers companies you have an application row for.**
  An email from a company you never applied to is not a candidate and never
  reaches the model, so the postings have to exist and be marked `applied` first.
* **The dashboard button does not lift `-from:me`.** `cli sync-email` has
  `--include-sent` for testing; the button deliberately does not, because your
  own application to a company must never be read as that company's answer. So
  the staged mail has to arrive **from a different account than the one Gmail is
  connected as**. A second Gmail, a university address, or a friend pressing send.
  A `you+demo@gmail.com` alias will not work; Gmail still counts it as you.

The companies below are invented on purpose. Do not stage a fabricated rejection
under a real company's name, even for a demo: it is a fake record of a real
organisation the moment anyone screenshots it.

### Step 1: three postings to reply to

On `/postings`, use **+ add posting** three times. Only company, title and
location matter for this.

| Company | Title | Location |
|---|---|---|
| `Northgate Robotics` | `Robotics Perception Intern` | `Zurich, Switzerland` |
| `Kestrel Analytics` | `Machine Learning Intern` | `London, United Kingdom` |
| `Vesper Labs` | `Research Intern, Applied ML` | `Berlin, Germany` |

Then set each one to **applied** (open it and press `2`; the keys are
`interested`, `applied`, `interviewing`, `offer`, `rejected`). The company name must
be stored exactly as written above, because the matcher looks for it in the
subject on word boundaries.

### Step 2: the three emails

Send these from your other account to the address Gmail is connected as. Keep
the subject lines exactly as written: the sender address will really be your
second account, so the subject is the only thing carrying the company name, and
it is what the matcher looks for. The `From` lines below are what the mail is
pretending to be, not something you have to arrange. The first line of each body
is what the classifier reads, so do not bury it.

---

**Email 1, an interview invitation**

> **From:** `Talent Team <talent@northgate-robotics.example>`
> **Subject:** `Northgate Robotics: next steps on your Robotics Perception Intern application`
>
> Hi Arash,
>
> Thanks for applying to the Robotics Perception Intern role. We would like to
> invite you to a first interview, a 45 minute technical conversation with two
> engineers from the perception team.
>
> Could you share a few times that work for you over the next two weeks? We are
> flexible between 09:00 and 18:00 CET.
>
> Best,
> Talent Team, Northgate Robotics

---

**Email 2, a rejection**

> **From:** `Recruiting <careers@kestrel-analytics.example>`
> **Subject:** `Your application to Kestrel Analytics`
>
> Hi Arash,
>
> Thank you for your interest in the Machine Learning Intern position. After
> reviewing your application, we have decided not to move forward at this time.
>
> We had a very large number of applicants this cycle and the decision was a
> close one. We would encourage you to apply again for future openings.
>
> Kind regards,
> Recruiting, Kestrel Analytics

---

**Email 3, deliberately not about an application**

> **From:** `Vesper Labs <newsletter@vesper-labs.example>`
> **Subject:** `Vesper Labs monthly: three papers from our applied ML group`
>
> Hi,
>
> Here is what our applied ML group published this month, plus two talks we are
> hosting in Berlin in October.
>
> You are receiving this because you subscribed to the Vesper Labs research
> newsletter.

The third one is the beat worth keeping. It matches a company you applied to,
reaches the model, and the model declines to suggest a status, so it goes into
the collapsed "not about an application" section instead of the queue. That is
the classifier being honest on camera rather than in a paragraph.

### Step 3: do not sync before you record

Send the mail, confirm it has arrived in the connected mailbox, and then leave it
alone. `cli sync-email` and the button both mark what they have examined, so a
rehearsal run consumes exactly the surprise you are trying to film.

If you want to rehearse anyway, stage six emails rather than three and use the
second set for the take.

### Step 4: the confidences you say out loud

The script says "ninety eight" and "ninety one". Those are plausible, not
guaranteed, and the real ones will be whatever the model returns on the day.
Either check them on a rehearsal set first and say the real numbers, or use the
version that needs none:

> **[you]** An interview invitation, a rejection, and one it decided was not
> about an application at all, which it folds away rather than guessing at.

### Step 5: clean up afterwards

The three postings are `source = manual`, so they are the only kind the app lets
you delete outright: open each one and use **delete**. Dismiss the three
suggestions in `/inbox`. Nothing else needs undoing, because a sync never touched
`applications` in the first place.

---

## Cutting it shorter

**A 60 second cut:** sections 2, 3 and 6, in that order. The Europe filter, the
agent search with the trace bar, and the inbox suggestion that refuses to act on
its own. Those three are what nothing else does.

**A 30 second silent GIF for the README:** grid scrolling, Europe filter landing
on 173, one chat query with results streaming, one trace bar hover, cut. No
letters, no inbox. Caption it in the README instead.

## Lines worth keeping if you improvise the rest

- "Public APIs, so nothing here is scraped."
- "Ninety seven point seven percent of locations resolve."
- "The bar adds up to the score. It is not decoration."
- "Me passing on a posting is a different fact from a company rejecting me."
- "It leaves a TODO instead of inventing it."
- "These are suggestions. Accepting one is what moves the application."
