import { expect, test } from '@playwright/test';

test.beforeEach(async ({ request }) => {
  const body = await (await request.get('/api/documents')).json();
  for (const document of body.documents) await request.delete(`/api/documents/${document.id}`);
});

test('导入、后台入库、问答、原文高亮及删除', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '把零散面经，变成你的知识库。' })).toBeVisible();
  await page.getByLabel('选择面经文件').setInputFiles({
    name: 'RAG 面经.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# RAG\n\nQ: 什么是 RAG？\nA: 先检索资料，再基于证据生成回答。'),
  });
  await expect(page.getByText('已入库', { exact: true })).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: '../doc/images/知识库.png', fullPage: true });
  await page.getByRole('button', { name: '检索问答' }).click();
  await page.getByLabel('你想了解什么？').fill('RAG 是什么？');
  await page.getByRole('button', { name: '查询知识库' }).click();
  await expect(page.getByText('先检索资料，再基于证据生成回答。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /RAG 面经.md · L3/ }).click();
  await expect(page.getByRole('complementary', { name: '来源原文' })).toBeVisible();
  await expect(page.locator('.source-lines .highlighted')).toHaveCount(2);
  await page.screenshot({ path: '../doc/images/问答与来源.png', fullPage: true });
  await page.getByRole('navigation').getByRole('button', { name: '知识库' }).click();
  await page.getByRole('button', { name: '删除 RAG 面经.md' }).click();
  await expect(page.getByText('还没有资料，导入第一份面经吧')).toBeVisible();
});

test('重复上传与无原文答案不会补写', async ({ page }) => {
  await page.goto('/');
  const file = {
    name: 'Agent 面经.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('Q: 如何设计 Agent？\n'),
  };
  await page.getByLabel('选择面经文件').setInputFiles(file);
  await expect(page.getByText('已入库', { exact: true })).toBeVisible({ timeout: 15_000 });
  await page.getByLabel('选择面经文件').setInputFiles(file);
  await expect(page.getByText(/文件已存在，无需重复导入/)).toBeVisible();
  await page.getByRole('button', { name: '检索问答' }).click();
  await page.getByLabel('你想了解什么？').fill('列出 2 道面试题');
  await page.getByRole('button', { name: '查询知识库' }).click();
  await expect(page.getByText('原文未提供答案', { exact: true })).toBeVisible();
  await expect(page.getByText(/请求 2 道，本次检索仅找到 1 道/)).toBeVisible();
});

test('模型超时与资料不足在页面上明确区分', async ({ page, request }) => {
  const upload = await request.post('/api/documents', {
    multipart: {
      files: {
        name: '诊断资料.md',
        mimeType: 'text/markdown',
        buffer: Buffer.from('Q: 什么是 RAG？\nA: 检索增强生成。'),
      },
    },
  });
  const id = (await upload.json()).results[0].document.id;
  await expect
    .poll(async () => (await (await request.get(`/api/documents/${id}`)).json()).status)
    .toBe('completed');
  await page.goto('/');
  await page.getByRole('button', { name: '检索问答' }).click();
  await page.getByLabel('你想了解什么？').fill('服务故障');
  await page.getByRole('button', { name: '查询知识库' }).click();
  await expect(page.getByRole('alert')).toContainText('模型服务超时');
  await expect(page.getByText('资料不足', { exact: true })).not.toBeVisible();
  await page.getByLabel('你想了解什么？').fill('知识库中不存在的资料');
  await page.getByRole('button', { name: '查询知识库' }).click();
  await expect(page.getByText('资料不足', { exact: true })).toBeVisible();
});

test('移动端知识库没有页面级横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: '选择文件', exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();
  await page.screenshot({ path: '../doc/images/移动端知识库.png', fullPage: true });
});
