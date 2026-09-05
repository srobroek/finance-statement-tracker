// Retry only pre-operation synchronization. Never wrap imports or mutations.
export async function downloadAndSyncBudget(api, syncId, options, sleep = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  for (let attempt = 1; ; attempt++) {
    try {
      await api.downloadBudget(syncId, options);
      await api.sync();
      return;
    } catch (error) {
      const transient = error?.code === "network-failure" || error?.code === "EPIPE" || error?.cause?.code === "EPIPE";
      if (!transient || attempt >= 3) throw error;
      await sleep(attempt * 250);
    }
  }
}
