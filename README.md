# Ora Attachments Exporter

Ora (ora.pm) is shutting down on Saturday, October 31, 2026. Its JSON and CSV exports only contain **links** to the files attached to your tasks (images, PDFs, etc.). Those files are stored on Amazon S3 and will disappear along with Ora.

This tool downloads those files to your computer and sorts them into folders: one per project, and one per task.

It comes with a second, optional script, **`ora_complement.js`**, that saves what the JSON export leaves out: the order of lists and tasks, custom fields, views, sprints, Team Chat… (see [step 1b](#step-1b--save-what-the-json-export-leaves-out-recommended)).

> ⏰ **Do it soon.** Once Ora shuts down, the files can no longer be downloaded.

---

## Step-by-step guide (Mac)

Allow about 15 minutes the first time. Nothing to buy, no account needed.

### What you need

- A Mac connected to the Internet.
- The file **`ora_attachments.py`** (this tool).
- Your **Ora exports**: one **`.json`** file per project (see step 1). The `.csv` export is only a last resort: it contains almost no attachments.
- Optional: **Google Chrome** (or another browser) for step 1b.
- You do **not** need the Ora app.

### Step 1 — Export your projects from Ora and prepare your folders

First, export **each project** as JSON, while Ora still works:

1. In Ora, open the project.
2. Click the **`…`** button, then **Export Project**.
3. At the very bottom of the window, under **Entire Project**, click **Export as JSON** (circled in red below). **Export as CSV**: isn't the correct format to locate attachments, as it contains almost none.

   <img src="docs/export-as-json.jpeg" alt="Ora's Export Project window, with the Export as JSON button circled in red" width="240">

4. A `.json` file named after the project is downloaded, usually into your **Downloads** folder.
5. Repeat for each project. Ora exports one project at a time, so it is easy to miss one: think of projects inside folders, in other organizations, and archived ones. The script of step 1b lists all your projects, which helps you check.

Then prepare your folders:

1. Open **Finder** and go to **Documents**.
2. Create a folder named **`Ora - Exports`** and drag all your `.json` files into it.
3. Still in **Documents**, create a second, **empty** folder named **`Ora - Attachments`**. The downloaded files will go there.
4. Put the file **`ora_attachments.py`** in **Documents**.

> ⚠️ The `Ora - Attachments` folder must **not** be inside the `Ora - Exports` folder. The tool refuses to write into the exports folder, to protect your exports.

### Step 1b — Save what the JSON export leaves out (recommended)

Ora's JSON export does not contain:

- the **order** of the lists, and of the tasks inside each list (an import elsewhere would come out shuffled);
- **custom fields**, list **automations** (workflows), **views**, **sprints** and story points, **recurring tasks**, task **templates**;
- your **folders** and **favorites**;
- **Team Chat** channels and messages.

The script **`ora_complement.js`** saves all of this in a single file, from your browser, while you are logged in to Ora. It takes a few minutes. Nothing reads this file yet: it holds Ora's own answers, unchanged, so that a future import into another tool can rebuild your projects as they were. Keep it with your exports.

1. Open [`ora_complement.js`](ora_complement.js) on GitHub and click the **Copy raw file** button (the two-squares icon above the code).
2. In Chrome, open **https://app.ora.pm** and log in.
3. Open the **console**: press **⌥ Option + ⌘ Command + J** (Windows and Linux: **Ctrl + Shift + J**).
4. The first time, Chrome refuses the paste and asks you to type **`allow pasting`**: type it, then press **Return**. This warning protects you: only paste code you trust. You can read this script first: it is short, it only reads, it only talks to Ora, and it does not put your session key in the file.
5. Paste (**⌘ Command + V**), then press **Return**.
6. Lines appear, one per project: `Project 3/38: …`. **Keep the tab open** until the line `Done` appears.
7. The file **`ora-complement-<date>.json`** is in your **Downloads** folder. Move it into **`Ora - Exports`**, with the other files. `ora_attachments.py` recognizes it and leaves it alone.

At the top of the script, `chat: true` saves Team Chat and `history: false` skips the full activity history of each project. That history needs many more requests; set it to `true` if you want it.

Other browsers: in **Firefox**, the console opens with **⌥ Option + ⌘ Command + K**. In **Safari**, first turn on **Settings → Advanced → Show features for web developers**, then press **⌥ Option + ⌘ Command + C**.

### Step 2 — Open Terminal

Terminal is a window where you type commands.

1. Press **⌘ Command** and **Space** at the same time.
2. Type **`Terminal`**, then press **Return**.

### Step 3 — Check that Python is there (only once)

In Terminal, type exactly this, then press **Return**:

```
python3 --version
```

- If you see `Python 3.` followed by a number, go to step 4.
- If a window offers to install the **"command line developer tools"**, click **Install**, accept, and wait until it finishes (about 10 minutes). Then type `python3 --version` again and press **Return**.

### Step 4 — Start the download

You will build a command by **dragging folders** into the Terminal window, so you never have to type a path. Put Finder and Terminal side by side.

1. In Terminal, type **`python3`** followed by **a space**.
2. Drag the file **`ora_attachments.py`** from Finder into the Terminal window.
3. Type **`export --exports`** followed by **a space**.
4. Drag the **`Ora - Exports`** folder into Terminal.
5. Type **`--out`** followed by **a space**.
6. Drag the **`Ora - Attachments`** folder into Terminal.
7. Type **`--pictures`**, then press **Return**.

The line should look like this (with your user name instead of `marie`):

```
python3 /Users/marie/Documents/ora_attachments.py export --exports /Users/marie/Documents/Ora\ -\ Exports --out /Users/marie/Documents/Ora\ -\ Attachments --pictures
```

The `\` before spaces are normal: Terminal adds them by itself when you drag a folder.

Lines scroll by, one per file, for example `[12/262] ok …`. **Keep the window open** until the word **`Summary`** appears. It takes a few minutes.

### Step 5 — Read the summary

At the end, you see a table:

```
Project                                ok  skipped  failed
...
TOTAL                                 265        0       1
```

- **ok**: files downloaded. 🎉
- **skipped**: files already there from a previous run, so they were skipped. That's normal.
- **failed**: files that could not be downloaded. The list is shown below the table.

If some files **failed**, run the same command again: press the **↑ arrow** key (the command comes back), then **Return**. The tool only fetches what is missing.

A **`missing_remote`** failure (code 403 or 404) means the file no longer exists at Ora. Running again won't change that.

### Step 6 — Check (optional, but recommended)

Type **`python3`** and a space, drag **`ora_attachments.py`**, type **`verify --out`** and a space, drag the **`Ora - Attachments`** folder, then press **Return**.

You should see `… file(s) verified without problems`. Files marked `missing_remote` are also listed as problems, which is normal.

### What you will find in `Ora - Attachments`

```
#My project/
  #489 - Task title/
    2457898 - image.png        ← the file, filed under its task
  _no-task/                    ← files whose task is not in the export
_pictures/                     ← project pictures and member photos
manifest.csv                   ← the list of all files (opens in Excel or Numbers)
external-links.csv             ← Google Drive, Figma… links for you to save yourself
```

---

## Troubleshooting

| What you see | What to do |
|---|---|
| `command not found: python3` | Do step 3 again. |
| `can't open file … ora_attachments.py` | The file is not where you think. Redo step 4, **dragging** the file instead of typing its name. |
| `Exports not found` | Redo step 4, dragging the `Ora - Exports` folder. |
| `is inside the exports folder (read-only)` | Your output folder is inside the exports folder. Move it next to it (step 1). |
| `TLS certificate error` | Open the **Applications** folder, then the **Python 3.x** folder, and double-click **Install Certificates.command**. Then run the command again. |
| The connection dropped, or you closed the window | Run the same command again: nothing is lost, the tool resumes where it stopped. |
| `WARNING: … read from CSV` | The `.json` file for that project is missing. The CSV contains almost no attachments (see below). Add the `.json` if you can. |

---

## Windows and Linux

- **Windows:** install Python from [python.org](https://www.python.org/downloads/), ticking **"Add python.exe to PATH"**. Then open **Command Prompt** and follow the same steps, typing `python` instead of `python3`. Pick an output folder with a short path, such as `C:\Ora`, because Windows limits path length.
- **Linux:** Python 3 is almost always already installed. Follow the same steps in a terminal.

---

## Reference

Python 3.9 or later. Standard library only: nothing to install.

```sh
# Download (resumes where it stopped if run again)
python3 ora_attachments.py export --exports "<folder or file>" --out "<folder>" [--source json|csv|auto] [--pictures] [--dry-run]

# Sort files you already downloaded some other way, without downloading anything
python3 ora_attachments.py relink --exports "<folder or file>" --files "<folder>" --out "<folder>"

# Check the files (presence, size, SHA-256). Non-zero exit code if there is a problem.
python3 ora_attachments.py verify --out "<folder>"
```

- `--source auto` (default) uses a project's JSON if present, otherwise its CSV.
- `--pictures` also saves project pictures and member photos into `_pictures/`.
- `--dry-run` shows the plan without writing or downloading anything.
- `relink` finds each file by its S3 key name (last segment of the URL), or by its SHA-256 if `--files` contains a `manifest.json`. Files are copied, never moved.

Manifest statuses: `ok`, `skipped` (already present and verified on a rerun), `failed`, `size_mismatch` (size differs from Ora's `fileSize`) and `missing_remote` (403 or 404: the file no longer exists on S3).

### CSV limitation

The CSV has **no attachment column** and only contains active tasks. The only files that can be found in it are the images embedded in task descriptions. In one real export, that was 15 files, versus 246 attachments in the JSON. The tool prints a warning whenever a project is read from CSV. Use the JSON whenever possible.

### What the tool does not do

- It **never** calls the Ora API (`api.ora.pm`, GraphQL, WebSocket) and never logs in to **any** account. It only makes HTTP GET requests to the public URLs found in the exports.
- `ora_complement.js` (step 1b) is the only part that talks to Ora. It runs in your browser, with the session you are logged in with, and only sends read requests from a fixed list, one at a time.
- It **never** writes into the exports folder.
- It does not download external links (Google Drive, Figma…): it lists them in `external-links.csv`.

### Privacy

S3 links are public, and JSON exports contain the members' email addresses. The `ora-complement` file also contains your Team Chat messages. Sharing the scripts is fine. Do not share your exports, your `ora-complement` file or your `Ora - Attachments` folder.

### Tests

```sh
python3 -m unittest discover -s tests
```

The tests use fictitious fixtures and a local fake S3 server. No real data is stored in this repository.

## License

MIT — see [LICENSE](LICENSE).

## Thanks

- [u/Logical_Speed_8622](https://www.reddit.com/user/Logical_Speed_8622/) on Reddit, for finding and fixing the bare S3 key bug in 1.1.0.
