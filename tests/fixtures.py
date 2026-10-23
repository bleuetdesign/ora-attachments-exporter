"""Fictitious fixtures that reproduce the tricky cases found in Ora exports.

No real data: identifiers, titles and contents are made up.
"""

import json
from pathlib import Path

ATT = "https://ora-attachments.s3.amazonaws.com/"
MSG = "https://message-attachments.s3.amazonaws.com/"
PRJ = "https://ora-projects.s3.amazonaws.com/"
PRO = "https://ora-profile-pictures.s3.amazonaws.com/"

# Content served by the fake S3, keyed by "<bucket>/<key>".
CONTENT = {
    "ora-attachments/1001aaa.png": b"PNG-1001" * 10,
    "message-attachments/1002bbb.png": b"PNG-1002" * 20,
    "ora-attachments/1003ccc.png": b"PNG-1003-shared",
    "ora-attachments/1005eee.svg": b"<svg>orphan</svg>",
    "ora-attachments/9999desc.png": b"PNG-description",
    "ora-attachments/1007ggg.png": b"PNG-1007",
    "ora-attachments/2001csv.png": b"PNG-csv-2001",
    "ora-projects/default-03.svg": b"<svg>project</svg>",
    "ora-profile-pictures/abcdef": b"\x89PNG-member",
}

SIZES = {k: len(v) for k, v in CONTENT.items()}


def _project_json():
    return {
        "project": {"id": 70001, "title": "Demo Project", "picture": PRJ + "default-03.svg?1715375227339",
                    "description": "See https://drive.google.com/drive/folders/XYZ"},
        "members": [{"id": 1, "userId": 501, "profilePicture": PRO + "abcdef?1698185997"}],
        "labels": [], "milestones": [], "taskTypes": [], "taskLabels": [], "taskRelations": [],
        "links": [], "checklists": [], "assignees": [], "timers": [],
        "lists": [{"id": 10, "title": "To do"}],
        "tasks": [
            {"id": 800001, "secondId": 12, "title": "Review: mockup / home", "listId": 10,
             "description": "See ![](" + ATT + "9999desc.png?id=9999) and " + ATT + "1001aaa.png"},
            # Duplicate secondId: same visible number, different task.
            {"id": 800002, "secondId": 12, "title": "Review: mockup / home", "listId": 99,
             "description": None},
            {"id": 800003, "secondId": 13, "title": "Archived task", "listId": 999, "description": ""},
        ],
        "taskComments": [
            {"id": 1, "taskId": 800001, "comment": "Figma: https://www.figma.com/file/abc/Demo)."},
        ],
        "attachments": [
            {"id": 1001, "uploadDate": "2024-06-17T18:06:06Z", "taskId": 800001, "url": ATT + "1001aaa.png",
             "externalUrl": "", "fileTitle": "image.png", "fileSize": str(SIZES["ora-attachments/1001aaa.png"]),
             "fileType": "png"},
            # Empty url, link in externalUrl; duplicate title containing ":".
            {"id": 1002, "uploadDate": "2024-06-18T10:00:00Z", "taskId": 800001, "url": None,
             "externalUrl": MSG + "1002bbb.png", "fileTitle": "2025-05-21T10:47:04,78-04:00.png",
             "fileSize": str(SIZES["message-attachments/1002bbb.png"]), "fileType": "image/png"},
            {"id": 1006, "uploadDate": "2024-06-18T10:00:00Z", "taskId": 800002, "url": "",
             "externalUrl": ATT + "1003ccc.png", "fileTitle": "2025-05-21T10:47:04,78-04:00.png",
             "fileSize": str(SIZES["ora-attachments/1003ccc.png"]), "fileType": "image/png"},
            # Same URL as 1006 on another task: a single download.
            {"id": 1003, "uploadDate": "2024-06-19T10:00:00Z", "taskId": 800003, "url": ATT + "1003ccc.png",
             "externalUrl": "", "fileTitle": "image.png", "fileSize": str(SIZES["ora-attachments/1003ccc.png"]),
             "fileType": "png"},
            # 404 on the fake S3.
            {"id": 1004, "uploadDate": "2024-06-20T10:00:00Z", "taskId": 800002, "url": ATT + "1004ddd.png",
             "externalUrl": "", "fileTitle": "lost.png", "fileSize": "123", "fileType": "png"},
            # taskId missing from the tasks.
            {"id": 1005, "uploadDate": "2024-06-21T10:00:00Z", "taskId": 123456, "url": ATT + "1005eee.svg",
             "externalUrl": "", "fileTitle": "diagram", "fileSize": str(SIZES["ora-attachments/1005eee.svg"]),
             "fileType": "image/svg+xml"},
            # Some exports put only the S3 key in "url", not a full URL.
            {"id": 1007, "uploadDate": "2024-06-22T10:00:00Z", "taskId": 800003, "url": "1007ggg.png",
             "externalUrl": "", "fileTitle": "last.png", "fileSize": str(SIZES["ora-attachments/1007ggg.png"]),
             "fileType": "png"},
        ],
    }


# Header as exported by Ora (the last column name depends on the account language).
CSV_HEADER = ("Global ID,List,ID,Title,Assignee,Milestone,Estimate,Tracked Time,Status,Task Type,Due Date,"
              "Updated,Description,Labels,Story points,Completed story points,Business value,Priorité")


def _csv_bytes():
    # Newline and "\r" inside a quoted field, URL with ?id=.
    row1 = ('900001,Backlog,7,"Title, with comma",,,,,Open,,,,"Line 1\r\nLine 2\rmore\n'
            '![](' + ATT + '2001csv.png?id=2001) end",,,,,')
    row2 = '900002,Backlog,8,No image,,,,,Open,,,,"Link https://drive.google.com/x",,,,,'
    return (CSV_HEADER + "\r\n" + row1 + "\r\n" + row2 + "\r\n").encode("utf-8")


def build_exports(root: Path) -> Path:
    """Creates a fictitious exports folder: one JSON+CSV project and one CSV-only project."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "Demo Project.json").write_text(json.dumps(_project_json(), ensure_ascii=False), encoding="utf-8")
    (root / "Demo Project.csv").write_bytes(_csv_bytes())
    (root / "CSV-only Project.csv").write_bytes(_csv_bytes())
    return root
