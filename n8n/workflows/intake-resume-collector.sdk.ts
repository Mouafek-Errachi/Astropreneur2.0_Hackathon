import { workflow, node, trigger, sticky, newCredential, expr } from '@n8n/workflow-sdk';

const gmailTrigger = trigger({
  type: 'n8n-nodes-base.gmailTrigger',
  version: 1.2,
  config: {
    name: 'Gmail: new CV email',
    parameters: {
      pollTimes: { item: [{ mode: 'everyMinute' }] },
      simple: false,
      filters: { q: 'has:attachment to:jobs@your-company.com' },
      options: { downloadAttachments: true }
    },
    credentials: { gmailOAuth2: newCredential('Gmail account') },
    output: [{ id: '18f2a1b2c3d4e5f6', subject: 'Application: Backend Engineer', from: { text: 'ana@example.com' } }]
  }
});

const emailToItems = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Email attachments to items',
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `const out = [];
const items = $input.all();
for (let i = 0; i < items.length; i++) {
  for (const [key, bin] of Object.entries(items[i].binary || {})) {
    const buffer = await this.helpers.getBinaryDataBuffer(i, key);
    out.push({ json: {
      source: 'email',
      source_ref: String(items[i].json.id + '-' + key).slice(0, 200),
      filename: String(bin.fileName || key).slice(0, 200),
      content_base64: buffer.toString('base64'),
    } });
  }
}
return out;`
    },
    output: [{ source: 'email', source_ref: '18f2a1b2c3d4e5f6-attachment_0', filename: 'cv.pdf', content_base64: 'JVBERi0xLjQK' }]
  }
});

const driveTrigger = trigger({
  type: 'n8n-nodes-base.googleDriveTrigger',
  version: 1,
  config: {
    name: 'Drive: new file in CVs folder',
    parameters: {
      pollTimes: { item: [{ mode: 'everyMinute' }] },
      triggerOn: 'specificFolder',
      folderToWatch: { __rl: true, value: '', mode: 'list' },
      event: 'fileCreated',
      options: {}
    },
    credentials: { googleDriveOAuth2Api: newCredential('Google Drive account') },
    output: [{ id: '1AbCdEfGhIjKlMnOp', name: 'cv-ana-silva.pdf', mimeType: 'application/pdf' }]
  }
});

const driveDownload = node({
  type: 'n8n-nodes-base.googleDrive',
  version: 3,
  config: {
    name: 'Download the file',
    parameters: {
      resource: 'file',
      operation: 'download',
      fileId: { __rl: true, value: expr('{{ $json.id }}'), mode: 'id' },
      options: {}
    },
    credentials: { googleDriveOAuth2Api: newCredential('Google Drive account') },
    output: [{ id: '1AbCdEfGhIjKlMnOp', name: 'cv-ana-silva.pdf' }]
  }
});

const driveToItem = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Drive file to item',
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `const out = [];
const items = $input.all();
for (let i = 0; i < items.length; i++) {
  for (const [key, bin] of Object.entries(items[i].binary || {})) {
    const buffer = await this.helpers.getBinaryDataBuffer(i, key);
    out.push({ json: {
      source: 'drive',
      source_ref: String(items[i].json.id || key).slice(0, 200),
      filename: String(bin.fileName || key).slice(0, 200),
      content_base64: buffer.toString('base64'),
    } });
  }
}
return out;`
    },
    output: [{ source: 'drive', source_ref: '1AbCdEfGhIjKlMnOp', filename: 'cv-ana-silva.pdf', content_base64: 'JVBERi0xLjQK' }]
  }
});

const applicationForm = trigger({
  type: 'n8n-nodes-base.formTrigger',
  version: 2.2,
  config: {
    name: 'Application form',
    parameters: {
      formTitle: 'Apply',
      formDescription: 'Upload your CV (PDF, Word, OpenDocument, RTF, text, or a photo).',
      formFields: {
        values: [
          {
            fieldLabel: 'CV',
            fieldType: 'file',
            multipleFiles: false,
            acceptFileTypes: '.pdf,.docx,.odt,.rtf,.txt,.html,.json,.jpg,.jpeg,.png,.webp',
            requiredField: true
          }
        ]
      },
      options: {}
    },
    output: [{ submittedAt: '2026-10-31T20:00:00.000Z', formMode: 'production' }]
  }
});

const formToItem = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Form upload to item',
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `const out = [];
const items = $input.all();
for (let i = 0; i < items.length; i++) {
  for (const [key, bin] of Object.entries(items[i].binary || {})) {
    const buffer = await this.helpers.getBinaryDataBuffer(i, key);
    out.push({ json: {
      source: 'form',
      source_ref: String('form-' + $execution.id + '-' + key).slice(0, 200),
      filename: String(bin.fileName || key).slice(0, 200),
      content_base64: buffer.toString('base64'),
    } });
  }
}
return out;`
    },
    output: [{ source: 'form', source_ref: 'form-123-CV', filename: 'cv.docx', content_base64: 'UEsDBBQ=' }]
  }
});

const jobBoardWebhook = trigger({
  type: 'n8n-nodes-base.webhook',
  version: 2.2,
  config: {
    name: 'Job board webhook',
    parameters: {
      httpMethod: 'POST',
      path: 'intake/job-board',
      authentication: 'headerAuth',
      responseMode: 'lastNode',
      options: {}
    },
    credentials: { httpHeaderAuth: newCredential('Job board webhook secret') },
    output: [{ body: { source_ref: 'jb-7', profile: { basics: { name: 'Dan Roe' }, skills: ['python'] } } }]
  }
});

const jobBoardToItem = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Job board payload to item',
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `return $input.all().map(item => {
  const b = item.json.body || item.json || {};
  const one = {};
  if (b.profile && typeof b.profile === 'object') one.profile = b.profile;
  else if (typeof b.text === 'string') one.text = b.text;
  else if (typeof b.content_base64 === 'string') one.content_base64 = b.content_base64;
  return { json: {
    job_id: Number.isInteger(b.job_id) ? b.job_id : undefined,
    source: 'job_board',
    source_ref: String(b.source_ref || '').slice(0, 200),
    filename: String(b.filename || '').slice(0, 200),
    ...one,
  } };
});`
    },
    output: [{ source: 'job_board', source_ref: 'jb-7', filename: '', profile: { basics: { name: 'Dan Roe' } } }]
  }
});

const buildRequest = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Build intake request',
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `const DEFAULT_JOB_ID = 1;
return $input.all().map(({ json }) => {
  const { job_id, ...item } = json;
  return { json: { job_id: job_id ?? DEFAULT_JOB_ID, items: [item] } };
});`
    },
    output: [{ job_id: 1, items: [{ source: 'email', source_ref: '18f2a1b2c3d4e5f6-attachment_0', filename: 'cv.pdf', content_base64: 'JVBERi0xLjQK' }] }]
  }
});

const sendToApi = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.2,
  config: {
    name: 'Send to HR API',
    parameters: {
      method: 'POST',
      url: 'http://host.docker.internal:8000/intake/resumes',
      authentication: 'genericCredentialType',
      genericAuthType: 'httpTemplatedCustomAuth',
      sendBody: true,
      specifyBody: 'json',
      jsonBody: expr('{{ JSON.stringify($json) }}'),
      options: { timeout: 120000 }
    },
    credentials: { httpTemplatedCustomAuth: newCredential('HR API upload key') },
    output: [{ filename: 'cv.pdf', accepted: true, candidate_id: 42, risk_level: 'none', flags: [], source: 'email', source_ref: '18f2a1b2c3d4e5f6-attachment_0' }]
  }
});

const setupNote = sticky(
  '## Setup\n' +
  '1. Dashboard > Sources (admin): create an upload-only key. Put it in the **HR API upload key** credential: headers template {"X-API-Key": "{{api_key}}"}.\n' +
  '2. Connect Gmail and Google Drive, set the Gmail search and the Drive folder.\n' +
  '3. Set `DEFAULT_JOB_ID` in **Build intake request** and the backend URL in **Send to HR API**.\n' +
  '4. n8n only forwards the CVs: every security check (file type, hidden text, prompt injection, duplicates) runs in the backend.',
  [buildRequest, sendToApi],
  { color: 4 }
);

export default workflow('hr-intake-collector', 'HR intake: collect CVs from every source')
  .add(gmailTrigger)
  .to(emailToItems)
  .to(buildRequest)
  .to(sendToApi)
  .add(driveTrigger)
  .to(driveDownload)
  .to(driveToItem)
  .to(buildRequest)
  .add(applicationForm)
  .to(formToItem)
  .to(buildRequest)
  .add(jobBoardWebhook)
  .to(jobBoardToItem)
  .to(buildRequest)
  .add(setupNote)
  .group('Send to the HR backend', [buildRequest, sendToApi], {
    description: 'One request per CV to POST /intake/resumes with the upload-only key; the backend runs every check'
  });
