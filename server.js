import express from 'express';
import path from 'path';
import fs from 'fs';
import crypto from 'crypto';
import jwt from 'jsonwebtoken';
import bcrypt from 'bcryptjs';
import multer from 'multer';
import Database from 'better-sqlite3';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PORT = Number(process.env.PORT || 10000);
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
const UPLOAD_DIR = process.env.UPLOAD_DIR || path.join(DATA_DIR, 'uploads');
const JWT_SECRET = process.env.JWT_SECRET || 'change-this-secret-in-render';
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || 'admin123';

for (const dir of [DATA_DIR, UPLOAD_DIR]) fs.mkdirSync(dir, { recursive: true });

const db = new Database(path.join(DATA_DIR, 'voters.sqlite'));
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

db.exec(`
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 username TEXT UNIQUE NOT NULL,
 email TEXT UNIQUE NOT NULL,
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'viewer',
 active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS permissions (
 user_id INTEGER PRIMARY KEY,
 upload INTEGER NOT NULL DEFAULT 0,
 search INTEGER NOT NULL DEFAULT 1,
 manage_users INTEGER NOT NULL DEFAULT 0,
 access_control INTEGER NOT NULL DEFAULT 0,
 history INTEGER NOT NULL DEFAULT 0,
 logs INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS uploads (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 file_name TEXT NOT NULL,
 file_path TEXT NOT NULL,
 username TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'Processing',
 voters INTEGER NOT NULL DEFAULT 0,
 pages INTEGER NOT NULL DEFAULT 0,
 error TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 completed_at TEXT
);
CREATE TABLE IF NOT EXISTS voters (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 upload_id INTEGER NOT NULL,
 serial_no TEXT NOT NULL,
 name TEXT,
 father_husband TEXT,
 relation TEXT,
 epic TEXT,
 age INTEGER,
 gender TEXT,
 house_no TEXT,
 part_no TEXT,
 page_no INTEGER,
 photo_key TEXT,
 raw_text TEXT,
 UNIQUE(upload_id, serial_no),
 FOREIGN KEY(upload_id) REFERENCES uploads(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_voters_serial ON voters(serial_no);
CREATE INDEX IF NOT EXISTS idx_voters_name ON voters(name);
CREATE INDEX IF NOT EXISTS idx_voters_epic ON voters(epic);
CREATE INDEX IF NOT EXISTS idx_voters_part ON voters(part_no);
CREATE TABLE IF NOT EXISTS logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER,
 username TEXT,
 action TEXT NOT NULL,
 detail TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
`);

function ensureAdmin() {
 const existing = db.prepare('SELECT id FROM users WHERE username=?').get('admin');
 if (!existing) {
   const hash = bcrypt.hashSync(ADMIN_PASSWORD, 12);
   const r = db.prepare('INSERT INTO users(username,email,password_hash,role,active) VALUES(?,?,?,?,1)').run('admin','admin@voter.local',hash,'admin');
   db.prepare('INSERT INTO permissions(user_id,upload,search,manage_users,access_control,history,logs) VALUES(?,?,?,?,?,?,?)').run(r.lastInsertRowid,1,1,1,1,1,1);
 }
}
ensureAdmin();

function logAction(userId, username, action, detail='') {
 db.prepare('INSERT INTO logs(user_id,username,action,detail) VALUES(?,?,?,?)').run(userId || null, username || 'System', action, detail);
}
function tokenFor(user) { return jwt.sign({ id:user.id, username:user.username, role:user.role }, JWT_SECRET, { expiresIn:'12h' }); }
function permissionsFor(userId) { return db.prepare('SELECT upload,search,manage_users,access_control,history,logs FROM permissions WHERE user_id=?').get(userId) || {}; }
function auth(req,res,next) {
 const h = req.headers.authorization || '';
 const t = h.startsWith('Bearer ') ? h.slice(7) : '';
 if (!t) return res.status(401).json({error:'Authentication required'});
 try {
   const p = jwt.verify(t, JWT_SECRET);
   const u = db.prepare('SELECT id,username,email,role,active FROM users WHERE id=?').get(p.id);
   if (!u || !u.active) return res.status(401).json({error:'Account is inactive or unavailable'});
   req.user=u; req.permissions=permissionsFor(u.id); next();
 } catch { return res.status(401).json({error:'Session expired. Please login again.'}); }
}
function requirePerm(name) { return (req,res,next)=> { if (req.user.role==='admin' || req.permissions[name]) return next(); return res.status(403).json({error:'Permission denied'}); }; }

const storage = multer.diskStorage({
 destination: (_req,_file,cb)=>cb(null,UPLOAD_DIR),
 filename: (_req,file,cb)=>{
   const ext = path.extname(file.originalname).toLowerCase() || '.pdf';
   cb(null, `${Date.now()}-${crypto.randomBytes(6).toString('hex')}${ext}`);
 }
});
// Accept either "file" or "pdf". Using any() prevents the Multer "Unexpected field"
// error when an older/newer frontend uses a different field name.
const upload = multer({
 storage,
 limits:{fileSize:50*1024*1024, files:1},
 fileFilter:(_req,file,cb)=>{
   const ext=path.extname(file.originalname).toLowerCase();
   if (ext !== '.pdf' && file.mimetype !== 'application/pdf') return cb(new Error('Only PDF files are allowed'));
   cb(null,true);
 }
});

const app=express();
app.disable('x-powered-by');
app.use(express.json({limit:'2mb'}));
app.use(express.urlencoded({extended:true}));
app.use(express.static(path.join(__dirname,'public')));

app.get('/health',(_req,res)=>res.json({ok:true,time:new Date().toISOString()}));

app.post('/api/login',(req,res)=>{
 const username=String(req.body.username||'').trim();
 const password=String(req.body.password||'');
 const u=db.prepare('SELECT id,username,email,password_hash,role,active FROM users WHERE username=? OR email=?').get(username,username);
 if(!u || !u.active || !bcrypt.compareSync(password,u.password_hash)) return res.status(401).json({error:'Invalid username/email or password'});
 logAction(u.id,u.username,'Login','Successful login');
 res.json({token:tokenFor(u),user:{id:u.id,username:u.username,email:u.email,role:u.role}});
});

app.get('/api/me',auth,(req,res)=>res.json({user:req.user,permissions:req.permissions}));

function dashboardData(){
 const voters=db.prepare('SELECT COUNT(*) c FROM voters').get().c;
 const pages=db.prepare('SELECT COALESCE(MAX(page_no),0) p FROM voters').get().p;
 const files=db.prepare("SELECT COUNT(*) c FROM uploads WHERE status='Completed'").get().c;
 const users=db.prepare('SELECT COUNT(*) c FROM users WHERE active=1').get().c;
 const recent=db.prepare(`SELECT u.*, COALESCE((SELECT COUNT(*) FROM voters v WHERE v.upload_id=u.id),0) AS voters, COALESCE((SELECT username FROM users x WHERE x.username=u.username LIMIT 1),u.username) AS username FROM uploads u ORDER BY u.id DESC LIMIT 10`).all();
 return {stats:{voters,pages,files,users},recent};
}
app.get('/api/dashboard',auth,requirePerm('search'),(req,res)=>res.json(dashboardData()));

function processPDF(pdfPath, uploadId, username) {
 return new Promise((resolve,reject)=>{
   const workerPath=path.join(__dirname,'ocr_worker.py');
   const py=spawn('python3',[workerPath,pdfPath],{stdio:['ignore','pipe','pipe']});
   let stdout=''; let stderr='';
   const timeout=setTimeout(()=>{ try{py.kill('SIGKILL')}catch{}; }, 15*60*1000);
   py.stdout.on('data',d=>stdout+=d.toString());
   py.stderr.on('data',d=>stderr+=d.toString());
   py.on('error',reject);
   py.on('close',code=>{
     clearTimeout(timeout);
     if(code!==0) return reject(new Error((stderr||`OCR worker exited with code ${code}`).slice(-4000)));
     let result;
     try { result=JSON.parse(stdout); } catch { return reject(new Error('OCR worker returned invalid JSON. '+stderr.slice(-1000))); }
     if(result && result.ok===false) return reject(new Error(result.error || 'OCR worker failed'));
     if(!Array.isArray(result.rows)) return reject(new Error('OCR worker returned no voter rows'));
     if(result.rows.length===0) return reject(new Error((result.warnings&&result.warnings[0]) || 'No voter records were detected in this PDF.'));
     const rows=result.rows.filter(r=>r && /^\d+$/.test(String(r.serial_no||'')));
     const insert=db.prepare(`INSERT OR REPLACE INTO voters(upload_id,serial_no,name,father_husband,relation,epic,age,gender,house_no,part_no,page_no,photo_key,raw_text) VALUES(@upload_id,@serial_no,@name,@father_husband,@relation,@epic,@age,@gender,@house_no,@part_no,@page_no,@photo_key,@raw_text)`);
     const tx=db.transaction(items=>{ db.prepare('DELETE FROM voters WHERE upload_id=?').run(uploadId); for(const r of items) insert.run({...r,upload_id:uploadId,serial_no:String(r.serial_no),part_no:String(r.part_no||result.part_no||'')}); });
     tx(rows);
     db.prepare('UPDATE uploads SET status=?,voters=?,pages=?,completed_at=CURRENT_TIMESTAMP,error=NULL WHERE id=?').run('Completed',rows.length,Number(result.pages||0),uploadId);
     logAction(null,username,'PDF processed',`${rows.length} voters, Part ${result.part_no||''}, ${result.pages||0} pages`);
     resolve({count:rows.length,pages:Number(result.pages||0),part_no:String(result.part_no||'')});
   });
 });
}

app.post('/api/upload',auth,requirePerm('upload'),(req,res,next)=>{
 upload.any()(req,res,async err=>{
   if(err) return res.status(400).json({error:err.message || 'Upload failed'});
   const file=(req.files||[])[0];
   if(!file) return res.status(400).json({error:'PDF file required'});
   const r=db.prepare('INSERT INTO uploads(file_name,file_path,username,status) VALUES(?,?,?,?)').run(file.originalname,file.path,req.user.username,'Processing');
   const uploadId=Number(r.lastInsertRowid);
   logAction(req.user.id,req.user.username,'Upload started',file.originalname);
   processPDF(file.path,uploadId,req.user.username).catch(e=>{
     db.prepare('UPDATE uploads SET status=?,error=?,completed_at=CURRENT_TIMESTAMP WHERE id=?').run('Failed',String(e.message||e).slice(-4000),uploadId);
     logAction(null,req.user.username,'PDF processing failed',String(e.message||e).slice(-1000));
   });
   res.json({success:true,uploadId,fileName:file.originalname});
 });
});

app.get('/api/history',auth,requirePerm('history'),(req,res)=>res.json(db.prepare('SELECT id,file_name,username,status,voters,pages,error,created_at,completed_at FROM uploads ORDER BY id DESC LIMIT 100').all()));
app.get('/api/logs',auth,requirePerm('logs'),(req,res)=>res.json(db.prepare('SELECT id,username,action,detail,created_at FROM logs ORDER BY id DESC LIMIT 200').all()));

app.get('/api/search',auth,requirePerm('search'),(req,res)=>{
 const q=String(req.query.q||'').trim();
 const field=String(req.query.field||'name');
 const gender=String(req.query.gender||'').trim();
 const age=String(req.query.age||'').trim();
 const part=String(req.query.part||'').trim();
 const page=String(req.query.page||'').trim();
 const limit=Math.min(Math.max(Number(req.query.limit||24),1),100);
 const offset=Math.max(Number(req.query.offset||0),0);
 const conditions=[]; const params=[];
 const col={name:'name',father:'father_husband',husband:'father_husband',epic:'epic',house:'house_no',serial:'serial_no'}[field] || 'name';
 if(q){ conditions.push(`LOWER(COALESCE(${col},'')) LIKE LOWER(?)`); params.push(`%${q}%`); }
 if(gender && gender!=='All') { conditions.push(`LOWER(COALESCE(gender,'')) LIKE LOWER(?)`); params.push(`%${gender}%`); }
 if(age && age!=='All') { const m=age.match(/(\d+)\s*[-–]\s*(\d+)/); if(m){conditions.push('age BETWEEN ? AND ?');params.push(Number(m[1]),Number(m[2]));} else if(/^\d+$/.test(age)){conditions.push('age=?');params.push(Number(age));} }
 if(part && part!=='All'){conditions.push('part_no=?');params.push(part)}
 if(page && page!=='All'){conditions.push('page_no=?');params.push(Number(page))}
 const where=conditions.length?'WHERE '+conditions.join(' AND '):'';
 const total=db.prepare(`SELECT COUNT(*) c FROM voters ${where}`).get(...params).c;
 const rows=db.prepare(`SELECT id,serial_no,name,father_husband,relation,epic,age,gender,house_no,part_no,page_no FROM voters ${where} ORDER BY CAST(serial_no AS INTEGER),id LIMIT ? OFFSET ?`).all(...params,limit,offset);
 res.json({total,rows,limit,offset});
});
app.get('/api/voters',auth,requirePerm('search'),(req,res)=>{
  const q=String(req.query.q||'').trim(); const limit=Math.min(Math.max(Number(req.query.limit||24),1),100); const offset=Math.max(Number(req.query.offset||0),0);
  const total=db.prepare('SELECT COUNT(*) c FROM voters WHERE name LIKE ? OR father_husband LIKE ? OR epic LIKE ? OR house_no LIKE ? OR serial_no LIKE ?').get(`%${q}%`,`%${q}%`,`%${q}%`,`%${q}%`,`%${q}%`).c;
  const rows=db.prepare('SELECT id,serial_no,name,father_husband,relation,epic,age,gender,house_no,part_no,page_no FROM voters WHERE name LIKE ? OR father_husband LIKE ? OR epic LIKE ? OR house_no LIKE ? OR serial_no LIKE ? ORDER BY CAST(serial_no AS INTEGER),id LIMIT ? OFFSET ?').all(`%${q}%`,`%${q}%`,`%${q}%`,`%${q}%`,`%${q}%`,limit,offset);
  res.json({total,rows,limit,offset});
});

app.get('/api/voter-photo/:id',(req,res,next)=>{
 const q=String(req.query.token||'');
 if(q && !req.headers.authorization) req.headers.authorization='Bearer '+q;
 auth(req,res,next);
},requirePerm('search'),async(req,res)=>{
 const v=db.prepare(`SELECT v.*,u.file_path FROM voters v JOIN uploads u ON u.id=v.upload_id WHERE v.id=?`).get(Number(req.params.id));
 if(!v || !v.file_path || !fs.existsSync(v.file_path)) return res.status(404).end();
 const m=String(v.photo_key||'').match(/^([^:]+):([^:]+):([^:]+)$/);
 if(!m) return res.status(404).end();
 const [page,x0,y0]=[Number(m[1]),Number(m[2]),Number(m[3])];
 const worker=spawn('python3',[path.join(__dirname,'photo_worker.py'),v.file_path,String(page),String(x0),String(y0)]);
 res.type('image/jpeg');
 worker.stdout.pipe(res);
 let err=''; worker.stderr.on('data',d=>err+=d.toString());
 worker.on('close',code=>{if(code!==0 && !res.headersSent)res.status(404).end();});
});

app.get('/api/export/:id',auth,requirePerm('history'),(req,res)=>{
 const uploadId=Number(req.params.id); const u=db.prepare('SELECT id,file_name,username,status,voters,pages,created_at FROM uploads WHERE id=?').get(uploadId); if(!u)return res.status(404).json({error:'Upload not found'});
 const voters=db.prepare('SELECT serial_no,name,father_husband,relation,epic,age,gender,house_no,part_no,page_no FROM voters WHERE upload_id=? ORDER BY CAST(serial_no AS INTEGER),id').all(uploadId);
 res.json({upload:u,voters});
});

app.get('/api/users',auth,requirePerm('manage_users'),(req,res)=>res.json(db.prepare('SELECT id,username,email,role,active,created_at FROM users ORDER BY id').all()));
app.post('/api/users',auth,requirePerm('manage_users'),(req,res)=>{
 const username=String(req.body.username||'').trim(), email=String(req.body.email||'').trim(), password=String(req.body.password||''), role=String(req.body.role||'viewer').toLowerCase();
 if(!username||!email||password.length<6)return res.status(400).json({error:'Username, email and password (6+ characters) are required'});
 if(!['admin','operator','viewer'].includes(role))return res.status(400).json({error:'Invalid role'});
 try{
   const r=db.prepare('INSERT INTO users(username,email,password_hash,role,active) VALUES(?,?,?,?,1)').run(username,email,bcrypt.hashSync(password,12),role);
   const defaults=role==='operator'?[1,1,0,0,1,0]:[0,1,0,0,0,0];
   if(role==='admin') defaults.splice(0,6,1,1,1,1,1,1);
   db.prepare('INSERT INTO permissions(user_id,upload,search,manage_users,access_control,history,logs) VALUES(?,?,?,?,?,?,?)').run(r.lastInsertRowid,...defaults);
   logAction(req.user.id,req.user.username,'User created',username);
   res.json({success:true});
 }catch(e){res.status(400).json({error:'Username or email already exists'});}
});
app.patch('/api/users/:id',auth,requirePerm('manage_users'),(req,res)=>{const id=Number(req.params.id);if(id===1&&req.body.active===false)return res.status(400).json({error:'Main admin cannot be disabled'});db.prepare('UPDATE users SET active=? WHERE id=?').run(req.body.active?1:0,id);logAction(req.user.id,req.user.username,'User status changed',String(id));res.json({success:true});});
app.delete('/api/users/:id',auth,requirePerm('manage_users'),(req,res)=>{const id=Number(req.params.id);if(id===1)return res.status(400).json({error:'Main admin cannot be deleted'});db.prepare('DELETE FROM users WHERE id=?').run(id);logAction(req.user.id,req.user.username,'User deleted',String(id));res.json({success:true});});
app.get('/api/access',auth,requirePerm('access_control'),(req,res)=>res.json(db.prepare(`SELECT u.id,u.username,u.role,u.active,p.upload,p.search,p.manage_users,p.access_control,p.history,p.logs FROM users u JOIN permissions p ON p.user_id=u.id ORDER BY u.id`).all()));
app.patch('/api/access/:id',auth,requirePerm('access_control'),(req,res)=>{const id=Number(req.params.id);const allowed=['upload','search','manage_users','access_control','history','logs'];for(const p of allowed)if(Object.prototype.hasOwnProperty.call(req.body,p))db.prepare(`UPDATE permissions SET ${p}=? WHERE user_id=?`).run(req.body[p]?1:0,id);logAction(req.user.id,req.user.username,'Permission updated',String(id));res.json({success:true});});

app.use((req,res)=>{ if(req.path.startsWith('/api/')) return res.status(404).json({error:'API endpoint not found'}); res.sendFile(path.join(__dirname,'public','index.html')); });

app.use((err,req,res,_next)=>{ console.error(err); res.status(500).json({error:err.message||'Server error'}); });
app.listen(PORT,'0.0.0.0',()=>console.log(`Punjabi Voter Search running on 0.0.0.0:${PORT}`));
