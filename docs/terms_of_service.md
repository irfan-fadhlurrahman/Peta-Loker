# Sources and their terms of service

*Read on 8 October 2026. This page records what each source's robots.txt and terms say, quoted, so a reader
can judge for themselves. It is not legal advice, and terms change: follow the links for the current text.*

This is a non-commercial portfolio project. It collects a small, recent slice (postings from the last 60
days) at a low request rate, with an honest User-Agent, respecting robots.txt. Contact details are removed
on ingestion, company names are replaced by pseudonyms in everything published, descriptions are cut to
200 characters, and the full raw pages are never redistributed. Content owners can ask for removal by
[opening an issue](../../../issues).

## Summary

| Source | robots.txt (pages used) | Terms of service | What the terms say about this kind of use | How this project uses it |
|---|---|---|---|---|
| Glints | Job pages allowed; the explore page with query parameters is disallowed (not used) | [glints.com/id/about/terms](https://glints.com/id/about/terms) (no date shown) | **Prohibits** "screen scraping, data mining, robots or similar data gathering and extraction tools" for reproducing site information without written consent; content is for "personal non-commercial use" | Job pages from the public sitemaps; JSON-LD only; masked, summarised output |
| Dealls | `Allow: /`; listing API host has no rules | [dealls.com/terms-and-condition](https://dealls.com/terms-and-condition) (modified 6 Oct 2026) | The only terms published are for paying business customers of its HR products; no visitor terms found. They restrict sharing content "tanpa persetujuan tertulis" (without written consent) | Public job-search API for discovery, job pages' JSON-LD for content; masked, summarised output |
| Loker.id | No restrictions | [loker.id/disclaimer](https://www.loker.id/disclaimer) (in force since 1 April 2007) | No clause on bots or automated access. Prohibits reproducing, copying or distributing "materi atau Konten LokerID" or using it commercially without written permission | Search listing and job pages; JSON-LD plus two HTML fields; masked, summarised output |
| Kalibrr | Only `/root` and `/candidate/profile` disallowed | [kalibrr.com/terms](https://www.kalibrr.com/terms) (March 2026) | Prohibits reproducing or distributing content "without written permission"; downloads allowed "solely for your personal and non-commercial use"; its free employer plan lists "automated extraction, scraping" as misuse | The job-search endpoint its own job board calls; masked, summarised output |
| KitaLulus | Only `/auth/` and `/my/` disallowed (www); the data API host `gql.kitalulus.com` disallows everything and is **not** used — the headless browser aborts the page's own requests to it | [kitalulus.com/syarat-dan-ketentuan](https://www.kitalulus.com/syarat-dan-ketentuan) (no date shown) | No clause naming bots. Prohibits "menggabungkan, menyalin, atau menggandakan … konten … termasuk lowongan pekerjaan" (combining, copying or duplicating content, including job listings) and reproducing content for public use | Public pages rendered in a headless browser; masked, summarised output |

## Clauses, quoted

### Glints
- *"All users are prohibited in using screen scraping, data mining, robots or similar data gathering and
  extraction tools on the Site for establishing, maintaining, advancing or reproducing information contained
  on our Site on your own website or in any other publication, except with our prior written consent."*
  (clause 2.3.9 of the PDF version)
- *"Users acknowledge and agree that the Contents are made available solely for their personal
  non-commercial use."*
- *"All users shall not … copy, reproduce, redistribute, republish or use any personally identifiable
  information about other users."*

### Dealls
- Terms titled *"Syarat dan Ketentuan Penggunaan Produk Dealls / KantorKu"*, addressed to business customers.
- Section 9: users agree not to *"mengungkapkan, menjual, membagi, memberikan konten, informasi, fitur,
  layanan apapun yang tersedia pada Produk tanpa persetujuan tertulis dari Dealls Group"* (disclose, sell,
  share or hand over any content or information on the Product without written consent).

### Loker.id
- Section F: *"Tidak diperkenankan mereproduksi, memodifikasi, menyalin atau mendistribusikan atau
  menggunakan untuk tujuan komersial setiap materi atau Konten LokerID di Situs Web LokerID tanpa izin
  tertulis dari LokerID."* (No reproducing, modifying, copying, distributing or commercial use of LokerID
  material without written permission.)

### Kalibrr
- *"You shall not reproduce, copy, distribute, upload, post, transmit, or disseminate in any manner … any
  content, graphics, pictures, or materials from our website or Application without written permission from
  Kalibrr and the relevant owners."*
- *"You may download relevant materials from our website … solely for your personal and non-commercial
  use."*
- Kalibrr Free (employers): *"Kalibrr reserves the right to monitor activity under Kalibrr Free to prevent
  misuse, abuse, automated extraction, scraping, fraudulent hiring activity…"*

### KitaLulus
- Section b: *"Pengguna tidak boleh mereproduksi, memodifikasi, menyalin atau mendistribusikan atau
  menggunakan untuk tujuan komersial hal apa pun di Layanan, tanpa izin tertulis dari KitaLulus."*
- Section c: users must not *"menggabungkan, menyalin, atau menggandakan dengan cara apa pun konten yang
  terdapat pada Layanan atau informasi yang tersedia pada Layanan, termasuk lowongan pekerjaan yang sudah
  kedaluwarsa"* and must not *"mereproduksi konten KitaLulus untuk penggunaan umum"*.

## Sources not used

| Source | Why |
|---|---|
| JobStreet | robots.txt disallows job pages (`*/job/`) and every URL with a query string for all crawlers |
| Karir.com | robots.txt disallows `/jobs/` |
| LinkedIn | Terms of service prohibit scraping; job data sits behind a login wall |
| Indeed | Terms of service prohibit automated access |
| TopKarir, Urbanhire, Jobs.id | Unreachable or returning errors when sources were chosen |

## Collection rules applied to every source

- robots.txt is read for every host and checked before every request (`core/http.py`); a disallowed URL is
  never fetched, and an unreachable robots.txt is treated as "disallow everything".
- 2–5 seconds between requests to the same site, at most 300 detail pages per source per run, once a day.
- User-Agent: `PetaLokerBot/0.1 (+https://github.com/irfan-fadhlurrahman/job_market; non-commercial
  portfolio project)` — no browser impersonation, no stealth plugins, no proxies.
- A 403, or a 429 that persists, stops the run for that source.
- Only postings from the last 60 days are kept.
