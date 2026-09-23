// Cloudflare Worker "kuji-waterlevel-scheduler".
// Its Cron Triggers are the primary scheduler for tanyeee/kuji-waterlevel-data. GitHub Actions
// `schedule` fires too irregularly for this project, so the workflows keep it only as a fallback.
//
// Required secret: GITHUB_DATA_REPO_TOKEN, a fine-grained PAT limited to
// tanyeee/kuji-waterlevel-data with the repository permission "Actions: Read and write".

const REPOSITORY = 'tanyeee/kuji-waterlevel-data';
const REF = 'main';

// Keys must match the Worker's Cron Triggers exactly. Cron Triggers run in UTC.
const WORKFLOW_BY_CRON = {
  '2,12,22,32,42,52 * * * *': 'publish-live.yml', // every 10 minutes
  '35 * * * *': 'sync-hourly.yml', // every hour at :35
  '25 18 * * *': 'archive.yml', // 03:25 JST every day
};

const RETRY_DELAY_MS = 3000;

async function dispatchWorkflow(workflow, token) {
  const url = `https://api.github.com/repos/${REPOSITORY}/actions/workflows/${workflow}/dispatches`;
  const request = {
    method: 'POST',
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      // GitHub's REST API rejects requests that carry no User-Agent.
      'User-Agent': 'kuji-waterlevel-scheduler',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    body: JSON.stringify({ ref: REF }),
  };

  let lastError;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      const response = await fetch(url, request);
      if (response.ok) return response.status;
      const detail = (await response.text()).slice(0, 300);
      lastError = new Error(`GitHub API returned ${response.status} for ${workflow}: ${detail}`);
      // A 4xx means a bad token, a missing permission or a wrong workflow name; retrying cannot fix it.
      if (response.status < 500) break;
    } catch (error) {
      lastError = error;
    }
    if (attempt === 1) await new Promise(resolve => setTimeout(resolve, RETRY_DELAY_MS));
  }
  throw lastError;
}

export default {
  async scheduled(controller, env) {
    const cron = String(controller.cron).trim().replace(/\s+/g, ' ');
    const workflow = WORKFLOW_BY_CRON[cron];
    if (!workflow) {
      throw new Error(`No workflow is mapped to Cron Trigger "${controller.cron}"`);
    }
    if (!env.GITHUB_DATA_REPO_TOKEN) {
      throw new Error('Secret GITHUB_DATA_REPO_TOKEN is not set');
    }
    const status = await dispatchWorkflow(workflow, env.GITHUB_DATA_REPO_TOKEN);
    console.log(JSON.stringify({ result: 'dispatched', cron, workflow, status }));
  },

  async fetch() {
    return new Response('kuji-waterlevel-scheduler runs on Cron Triggers only.\n', {
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  },
};
