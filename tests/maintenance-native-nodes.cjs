// Exercise installed n8n node implementations with synthetic bytes and a mocked network boundary.
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const root=process.argv[2],workflow=JSON.parse(fs.readFileSync(process.argv[3]));
const moduleAt=p=>require(path.join(root,'n8n-nodes-base/dist',p));
const extract=moduleAt('nodes/Files/ExtractFromFile/actions/moveTo.operation.js');
const convert=moduleAt('nodes/Files/ConvertToFile/actions/toJson.operation.js');
const graph=moduleAt('nodes/Microsoft/OneDrive/GenericFunctions.js');
const {MicrosoftOneDrive}=moduleAt('nodes/Microsoft/OneDrive/MicrosoftOneDrive.node.js');
const node=name=>workflow.nodes.find(n=>n.name===name);
const ctx=(n,items,overrides={})=>({getWorkflowSettings:()=>({}),getNode:()=>n,getInputData:()=>items,getNodeParameter:(k,i,d)=>k in overrides?overrides[k]:k in n.parameters?n.parameters[k]:d,continueOnFail:()=>false,helpers:{
assertBinaryData:(i,k)=>items[i].binary[k],getBinaryDataBuffer:async(i,k)=>Buffer.from(items[i].binary[k].data,'base64'),detectBinaryEncoding:()=> 'utf8',
prepareBinaryData:async(b,fileName,mimeType)=>({data:b.toString('base64'),fileName,mimeType}),returnJsonArray:x=>[x].flat().map(json=>({json})),constructExecutionMetaData:x=>x}});
(async()=>{
 const plan={schema_version:'actual-adcb-reconstruction-v1',plan_sha256:'a'.repeat(64)};
 const input=[{json:{},binary:{data:{data:Buffer.from(JSON.stringify(plan)).toString('base64')}}}];
 const extracted=await extract.execute.call(ctx(node('Extract Immutable Maintenance Plan'),input),input,'fromJson');
 assert.deepEqual(extracted[0].json,{maintenance_plan:plan});
 const receipt={schema_version:'actual-maintenance-receipt-v1',state:'PARTIAL',remaining_actions:1};
 const converted=await convert.execute.call(ctx(node('Convert Redacted Maintenance Receipt'),[{json:{receipt}}]),[{json:{receipt}}]);
 assert.deepEqual(JSON.parse(Buffer.from(converted[0].binary.data.data,'base64')),{receipt});
 let endpoint,body;graph.microsoftApiRequest=async(method,url,bytes)=>{endpoint=url;body=bytes;return JSON.stringify({id:'synthetic-receipt'});};
 const upload=node('Archive Redacted Maintenance Receipt');
 const context=ctx(upload,converted,{fileName:'approved-execution-hash.maintenance-receipt.json',parentId:'synthetic-parent',authentication:'microsoftOneDriveOAuth2Api'});
 await new MicrosoftOneDrive().execute.call(context);
 assert.ok(endpoint.endsWith('/approved-execution-hash.maintenance-receipt.json:/content'));assert.deepEqual(JSON.parse(body),{receipt});
 graph.microsoftApiRequest=async()=>{throw Error('SYNTHETIC_ARCHIVE_FAILURE');};
 await assert.rejects(new MicrosoftOneDrive().execute.call(context),/SYNTHETIC_ARCHIVE_FAILURE/);
 console.log('PASS native extraction, JSON serialization, unique OneDrive filename and archive failure');
})().catch(e=>{console.error(e);process.exit(1);});
