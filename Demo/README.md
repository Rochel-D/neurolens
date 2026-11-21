# Demo

**Live app:** https://neurolens-pcpbqia5gq-el.a.run.app

> The live app runs in demo mode: accounts and reports reset whenever the server restarts. The first visit after it
> has been idle takes 10–20 seconds to load.

## Demo steps (about 5 minutes)

Sample files are in `Source-Code/sample_data/reports/`.

1. **Sign in.** Create an account with **Create account**, or sign in as admin.
2. **Upload a blood report.** Reports → upload `cbc_report.txt`. Point out the summary tiles, the low, high and
   critical flags, and the range bars.
3. **Explain it.** Click **Explain**. Show the answer in **Me (simple)**, then switch to **My doctor**. Point out the
   numbered sources and the "verified" tag under the answer.
4. **Change language.** Switch the language to **Hindi** or **Konkani** and ask again; the numbers stay the same.
5. **Make a PDF.** Type "Make a PDF of this" in the chat and open the downloaded file.
6. **Trends.** Upload `metabolic_report.txt`, then a second metabolic report, and open **Trends** and **Compare**.
7. **Brain MRI.** Brain MRI → upload a greyscale MRI slice. Show the prediction, the probabilities and the Grad-CAM
   heatmap.
8. **Guardrail.** In a new chat, type "I have crushing chest pain right now". The agent stops and shows the 112
   message. Then ask "What causes chest pain?", which is answered normally.
9. **Admin.** Sign in as admin and show **API keys**, **Traces** (the steps of each answer) and **System Review**.

Screenshots of each of these steps are in [`../Screenshots`](../Screenshots/README.md).
