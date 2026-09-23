# Free deploy: Oracle Cloud Always Free

Why this one: it is the only genuinely free option where this app works
*properly* — a real VM with a real disk, so MySQL and the `uploads/` folder
both survive restarts. Render's free tier sleeps and has no persistent disk,
which would silently lose every profile photo and exam paper.

**What you get, free forever:** 2 OCPU / 12 GB RAM ARM (Ampere A1), 200 GB block
storage, 10 TB/month egress, 2 tiny AMD VMs as well.
Oracle [quietly halved the ARM allowance](https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/)
from 4 OCPU / 24 GB in June 2026 — 2/12 is still far more than this app needs.

**What it costs you anyway:** a credit/debit card for identity verification
(Oracle places a temporary ~$1 / ₹80 hold and refunds it). An Always Free
account cannot be charged — there is nothing to bill unless you explicitly
upgrade.

---

## Step 1 — Create the account

Go to <https://www.oracle.com/cloud/free/> → **Start for free**. The form runs in
stages; each one has a gotcha.

**a. Country + email.** Country is India. Use an address you can open right now —
Oracle emails a verification link before anything else happens.

**b. Account (tenancy) name.** This becomes part of your console URL and is
effectively permanent. Lowercase, no spaces — e.g. `paralleldots`. It is *not*
your username.

**c. Home region — the one irreversible choice on this page.**
Every Always Free resource must live in your home region, and you cannot move a
tenancy later. From India: **India South (Hyderabad)** or India West (Mumbai).
Both are single-availability-domain regions, which matters for capacity retries
(see Step 3).

**d. Address + phone → SMS OTP.**

**e. Card verification.** Oracle authorises a small amount (~₹75–₹80) and
releases it; an Always Free account has nothing to bill. Practical notes:

- Credit cards clear far more often than debit cards.
- Virtual, prepaid and most RuPay cards are rejected outright.
- Indian cards sometimes need the 3-D Secure OTP twice. That is normal.
- If the card is refused, the account stalls here — a different card is the only
  real fix; Oracle support rarely overrides it.

**f. Wait for the "Your account is ready" email** — usually minutes, occasionally
an hour. Sign in at <https://cloud.oracle.com> with the tenancy name from (b).

You land on a **30-day $300 trial that runs alongside Always Free**. When the
trial ends, anything tagged *Always Free-eligible* keeps running for good and
everything else is stopped — which is why the shape choice in Step 3 matters.

## Step 2 — Create an SSH key on your Mac

```bash
ssh-keygen -t ed25519 -C "oracle-school-api" -f ~/.ssh/oracle_school
pbcopy < ~/.ssh/oracle_school.pub     # public key now on your clipboard
```

## Step 3 — Create the instance

Hamburger menu **☰ → Compute → Instances → Create instance**.

The form is long but only six things matter. **Set the shape before the image** —
picking the ARM shape filters the image list down to builds that actually run on
ARM, which removes a whole class of "why won't it boot" problems.

**1. Name** — `school-api`. Leave the compartment as the root default.

**2. Placement / availability domain** — Hyderabad and Mumbai each have exactly
one AD, so there is nothing to choose. Leave it. (This is why the usual
"try AD-2" advice for capacity errors does not apply to Indian regions.)

**3. Shape** — click **Change shape**:

- Instance type: **Virtual machine**
- Shape series: **Ampere** (Arm-based)
- Shape: **VM.Standard.A1.Flex**
- OCPUs: **2** — memory auto-fills to **12 GB**

Confirm the **"Always Free-eligible"** chip is showing. If it is not, you are
about to build something that gets shut off when the trial ends.

**4. Image** — **Change image → Canonical Ubuntu → 24.04**. The default is Oracle
Linux; the bootstrap script handles both, but the instructions here assume Ubuntu
(login user `ubuntu`, vs `opc` on Oracle Linux).

**5. Networking** — accept **Create new virtual cloud network**, and make sure
**Assign a public IPv4 address** is **Yes**. Without it the machine has no route
in and you would have to rebuild.

**6. SSH keys** — select **Paste public keys** and paste the contents of
`~/.ssh/oracle_school.pub` from Step 2. Not the private key. Oracle never shows
this again, and there is no password login — lose the key and the machine is a
brick.

**Boot volume** — tick *Specify a custom boot volume size* and set **100 GB**.
The Always Free budget is
[200 GB total across every volume](https://docs.oracle.com/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
default is 50 GB, minimum is 47 GB. 100 GB leaves room for a second machine later.

Click **Create**. It reaches **RUNNING** in about a minute. Copy the
**Public IP address** from the instance page — everything after this needs it.

### If you get "Out of host capacity"

Very common on free ARM, and not a mistake on your part — that region genuinely
has no free A1 cores at that moment.

- **Retry.** Capacity frees up constantly; people report success within hours,
  often at odd times of day.
- **Ask for less** — 1 OCPU / 6 GB still runs this app comfortably. Smaller
  requests are satisfied more often.
- **You cannot switch region.** Always Free only works in your home region, so
  the fix is time, not geography.
- **Pay-As-You-Go upgrade** gets near-instant capacity and keeps Always Free
  resources free — but billing goes live, so real charges become possible if you
  ever exceed the free allowances. Only if you accept that.

## Step 4 — Open ports 80 and 443 in the console

The instance is created behind a virtual firewall that allows **only SSH**. Two
separate firewalls have to be opened; this step is the cloud-side one.

**☰ → Networking → Virtual cloud networks →** click your VCN **→ Security Lists**
(left sidebar, under *Resources*) **→ Default Security List for vcn-… →
Add Ingress Rules**.

Fill in exactly:

| Field | Value |
|---|---|
| Stateless | **unchecked** |
| Source Type | CIDR |
| Source CIDR | `0.0.0.0/0` |
| IP Protocol | TCP |
| Source Port Range | *leave blank* (means all) |
| Destination Port Range | `80` |
| Description | `HTTP` |

Click **+ Another Ingress Rule** and repeat with Destination Port `443`
(`HTTPS`), then **Add Ingress Rules**.

Leaving *Source Port Range* blank is the bit people get wrong — filling in `80`
there matches the client's outgoing port, not yours, and silently blocks
everything.

That is half the job. Oracle's Ubuntu image **also** ships local iptables rules
that reject 80/443 before they reach Caddy — the single most common reason a
"correctly configured" Oracle instance still times out.
`bootstrap-oracle.sh` fixes that second firewall for you in Step 6.

## Step 5 — Upload the project

From your Mac:

```bash
cd ~/Desktop/IRCTC_Clone/shelfwatch_sfa_ir_backend
rsync -av -e "ssh -i ~/.ssh/oracle_school" \
  --exclude venv --exclude .venv --exclude __pycache__ \
  --exclude .env --exclude .env.production --exclude uploads --exclude .git \
  ./ ubuntu@YOUR_PUBLIC_IP:/tmp/school-api/

ssh -i ~/.ssh/oracle_school ubuntu@YOUR_PUBLIC_IP \
  'sudo mkdir -p /opt/school-api && sudo cp -r /tmp/school-api/. /opt/school-api/ && sudo chown -R ubuntu:ubuntu /opt/school-api'
```

## Step 6 — One command on the server

```bash
ssh -i ~/.ssh/oracle_school ubuntu@YOUR_PUBLIC_IP
cd /opt/school-api
sudo bash bootstrap-oracle.sh
```

That installs Docker, adds swap, fixes the local firewall, generates
`.env.production` with random secrets, builds the images, starts MySQL + API +
Caddy, and installs the nightly backup cron. First build takes 3–6 minutes on
ARM.

When it prints `Done`, open `http://YOUR_PUBLIC_IP/docs`.

## Step 7 — Log in and change the password immediately

Default seeded account is `admin` / `admin123`, and it is published in your
README. Change it, and the `principal` / `subadmin` ones too, before anything
real goes in.

## Step 8 — Add HTTPS (free, 5 minutes)

Right now logins travel in plain HTTP. To fix it you need a hostname. Any of:

- A domain you own → add an **A record** to the public IP.
- Free subdomain via <https://www.duckdns.org> → e.g. `yourschool.duckdns.org`.

Then:

```bash
cd /opt/school-api
sudo sed -i 's/^SITE_ADDRESS=.*/SITE_ADDRESS=yourschool.duckdns.org/' .env.production
sudo sed -i 's/^TLS_EMAIL=.*/TLS_EMAIL=you@yourmail.com/' .env.production
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

Caddy fetches a Let's Encrypt certificate on the first request. Watch it with
`docker compose -f docker-compose.prod.yml --env-file .env.production logs -f caddy`.

---

## Things that will bite you later

- **Keep a copy of `/opt/school-api/.env.production`.** Lose `MYSQL_PASSWORD`
  and you cannot reconnect to the existing database volume.
- **Oracle reclaims idle Always Free compute.** A VM serving real traffic is
  fine; one sitting at 0% for weeks can be flagged. The nightly backup cron
  gives it a heartbeat.
- **Backups live on the same disk.** Run `scp` them off, or the day the volume
  dies you lose both. `backup.sh` writes to `/opt/school-api/backups`.
- **ARM builds.** Both `python:3.11-slim` and `mysql:8.0` publish
  [arm64v8 images](https://hub.docker.com/_/mysql), so nothing special is needed
  — but if you ever add a dependency with no ARM wheel, the build will fail there.
- The [pre-launch checklist in `DEPLOY.md`](./DEPLOY.md) still applies: wide-open
  CORS, SHA-256 passwords, public `/docs`, simulated SMS.
