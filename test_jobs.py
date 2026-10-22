import asyncio
import sys
import os
from playwright.async_api import async_playwright

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

HTML_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "index.html")).replace("\\", "/")
FILE_URL = f"file:///{HTML_PATH}"

async def run_tests():
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        def on_console(msg):
            if msg.type == 'error':
                errors.append(f"Console error: {msg.text}")
        page.on('console', on_console)
        page.on('pageerror', lambda err: errors.append(f"Page error: {err}"))

        print(f">>> Navigating to {FILE_URL}...")
        await page.goto(FILE_URL)
        await page.wait_for_timeout(1000)

        # 1. Check title & canvas
        title = await page.title()
        print(f"Page Title: {title}")
        canvas = await page.query_selector("#packetCanvas")
        assert canvas is not None, "packetCanvas not found"

        # 2. Check that JSESSIONID contains the AP name and clients have targetAp
        print("\n--- Testing AP-based JSESSIONID Generation & Sticky AP Mapping ---")
        clients_data = await page.evaluate("""() => {
            return State.clients.slice(0, State.clientCount).map(c => ({
                id: c.id,
                name: c.name,
                sessionId: c.sessionId,
                targetWeb: c.targetWeb,
                targetAp: c.targetAp,
                system: c.system
            }));
        }""")
        for c in clients_data:
            print(f"  {c['name']}: JSESSIONID={c['sessionId']} -> Sticky: {c['targetWeb']} -> {c['targetAp']}")
            assert c['targetAp'] is not None, f"{c['name']} must have targetAp"
            ap_tag = c['targetAp'].replace('-', '')
            assert ap_tag in c['sessionId'], f"JSESSIONID {c['sessionId']} must contain AP identifier {ap_tag}"

        # 3. Verify packets are strictly routed to the assigned targetAp
        print("\n--- Verifying Web-AP Routing sends packets to the AP that generated the JSESSIONID ---")
        await page.wait_for_timeout(1500)
        routed_packets = await page.evaluate("""() => {
            return packets.filter(p => p.apNode && p.clientRef && p.clientRef.sessionId).map(p => ({
                clientName: p.clientRef.name,
                expectedAp: p.clientRef.targetAp,
                actualAp: p.apNode.name
            }));
        }""")
        print(f"  Checked {len(routed_packets)} in-flight packets with active sessions:")
        assert len(routed_packets) > 0, "There should be in-flight packets routed to AP"
        for p_info in routed_packets[:8]:
            print(f"    {p_info['clientName']}: expected {p_info['expectedAp']} == actual {p_info['actualAp']}")
            assert p_info['expectedAp'] == p_info['actualAp'], f"Packet from {p_info['clientName']} must route to {p_info['expectedAp']}, but went to {p_info['actualAp']}"

        # 3-B. Verify that JSESSIONID remains completely unchanged across multiple normal requests
        print("\n--- Verifying JSESSIONID is NOT updated per request (kept static until error) ---")
        initial_sessions = await page.evaluate("""() => {
            return State.clients.slice(0, State.clientCount).map(c => ({ id: c.id, sessionId: c.sessionId }));
        }""")
        await page.wait_for_timeout(2500) # Wait 2.5s while packets keep flowing
        current_sessions = await page.evaluate("""() => {
            return State.clients.slice(0, State.clientCount).map(c => ({ id: c.id, sessionId: c.sessionId }));
        }""")
        for init_s in initial_sessions:
            cur_s = next(c for c in current_sessions if c['id'] == init_s['id'])
            assert init_s['sessionId'] == cur_s['sessionId'], f"Client {init_s['id']} JSESSIONID changed from {init_s['sessionId']} to {cur_s['sessionId']} without error!"
        print("  Confirmed: JSESSIONID remained 100% unchanged during continuous traffic without error.")

        # 4. Test Single AP Failover (drop AP-02, client receives error -> new JSESSIONID on return -> stays static)
        print("\n--- Testing Single AP Failure & Re-assignment by New AP ---")
        # Find a client bound to AP-02
        c_ap02 = next((c for c in clients_data if c['targetAp'] == 'AP-02'), None)
        if c_ap02:
            print(f"  Found client bound to AP-02: {c_ap02['name']} ({c_ap02['sessionId']})")
            # Drop AP-02
            await page.click("#card-ap_02")
            await page.wait_for_timeout(400)
            status_ap02 = await page.locator("#lb-badge-ap_02").inner_text()
            print(f"  AP-02 status after drop: {status_ap02}")
            assert "down/-" in status_ap02

            # Wait for failover to another AP (e.g. AP-04) and new JSESSIONID re-assignment
            reassigned = False
            new_session_id = None
            for sec in range(1, 16):
                await page.wait_for_timeout(800)
                cur_client = await page.evaluate(f"""() => {{
                    return State.clients.find(c => c.id === {c_ap02['id']});
                }}""")
                if cur_client['targetAp'] != 'AP-02' and cur_client['sessionId'] is not None and 'AP02' not in cur_client['sessionId']:
                    new_session_id = cur_client['sessionId']
                    print(f"  Sec {sec}: {c_ap02['name']} successfully reassigned to new AP {cur_client['targetAp']} with new JSESSIONID: {new_session_id}!")
                    reassigned = True
                    break
            assert reassigned == True, f"{c_ap02['name']} should be re-assigned to another active AP after AP-02 died"

            # Verify that AFTER failover and re-assignment, JSESSIONID STAYS STATIC and is NOT updated per request!
            print("  Verifying new JSESSIONID stays static after failover (not regenerated per request)...")
            await page.wait_for_timeout(2500)
            after_client = await page.evaluate(f"""() => {{
                return State.clients.find(c => c.id === {c_ap02['id']});
            }}""")
            assert after_client['sessionId'] == new_session_id, f"Failover JSESSIONID should remain static, but changed from {new_session_id} to {after_client['sessionId']}"
            print(f"  Confirmed: New JSESSIONID ({new_session_id}) remained static across multiple post-failover requests!")

            # Recover AP-02
            await page.click("#card-ap_02")
            await page.wait_for_timeout(500)

        # 5. Check Sessions Tab with AP Column
        print("\n--- Testing Sessions Tab UI with AP Column ---")
        await page.click("#rtab-btn-sessions")
        await page.wait_for_timeout(500)
        table_text = await page.locator("#right-tab-content").inner_text()
        print(f"  Session tab preview: {table_text[:140].strip()}...")
        assert "固定先AP" in table_text or "到達AP" in table_text
        assert "AP-0" in table_text
        # Switch back to jobs
        await page.click("#rtab-btn-jobs")
        await page.wait_for_timeout(300)

        # 5-B. Test Graceful Drain during "WEB-A系切離-waitあり":
        # Existing A-system clients MUST stay on System A during wait time (NOT routed to System B)!
        print("\n--- Testing Graceful Drain (WEB-A系切離-waitあり) ---")
        btn_cut_a_wait = await page.wait_for_selector('button:has-text("WEB-A系切離-waitあり")', timeout=3000)
        await btn_cut_a_wait.click()
        await page.wait_for_timeout(500)

        # Confirm Web-02 has drainTimer > 0
        drain_val = await page.evaluate("() => State.nodes.web_02.drainTimer")
        print(f"  Web-02 drainTimer: {drain_val}s")
        assert drain_val > 0, "Web-02 should have active drain timer"

        # Check in-flight packets during drain: clients bound to Web-A must STILL route to Web-A!
        await page.wait_for_timeout(1000)
        drain_packets = await page.evaluate("""() => {
            return packets.filter(p => !p.isReturning && p.clientRef && (p.clientRef.targetWeb === 'Web-02' || p.clientRef.targetWeb === 'Web-04') && p.clientRef.sessionId).map(p => ({
                clientName: p.clientRef.name,
                targetWeb: p.clientRef.targetWeb,
                routedSys: p.system,
                webName: p.webNode ? p.webNode.name : null
            }));
        }""")
        print(f"  In-flight forward packets for Web-A clients during wait period: {len(drain_packets)}")
        assert len(drain_packets) > 0, "There should be active in-flight packets for Web-A clients"
        for dp in drain_packets[:8]:
            print(f"    {dp['clientName']} (Target: {dp['targetWeb']}) -> Routed to: {dp['routedSys']} ({dp['webName']})")
            assert dp['routedSys'] == 'A', f"During wait period, client bound to {dp['targetWeb']} must stay on System A, but routed to {dp['routedSys']}"
        print("  Confirmed: During wait period, existing sessions stay on their system and do NOT jump to the other system.")

        # Now recover Web-A
        btn_in_a = await page.wait_for_selector('button:has-text("WEB-A系組込")', timeout=3000)
        await btn_in_a.click()
        await page.wait_for_timeout(500)

        # 6. Test 9 Operation Jobs
        print("\n--- Testing 9 Operation Jobs ---")
        jobs_to_test = [
            ("WEB-A系切離-waitあり", "#lb-badge-web_02", "under-maintenance", "#lb-badge-web_04", "under-maintenance"),
            ("WEB-A系組込", "#lb-badge-web_02", "up/active", "#lb-badge-web_04", "up/active"),
            ("WEB-A系切離-waitなし", "#lb-badge-web_02", "under-maintenance", "#lb-badge-web_04", "under-maintenance"),
            ("WEB-A系組込", "#lb-badge-web_02", "up/active", "#lb-badge-web_04", "up/active"),
            ("WEB-B系切離-waitあり", "#lb-badge-web_01", "under-maintenance", "#lb-badge-web_03", "under-maintenance"),
            ("WEB-B系組込", "#lb-badge-web_01", "up/active", "#lb-badge-web_03", "up/active"),
            ("WEB-B系切離-waitなし", "#lb-badge-web_01", "under-maintenance", "#lb-badge-web_03", "under-maintenance"),
            ("WEB-B系組込", "#lb-badge-web_01", "up/active", "#lb-badge-web_03", "up/active"),
            ("WEB全台切離-waitあり", "#lb-badge-web_01", "under-maintenance", "#lb-badge-web_02", "under-maintenance"),
            ("WEB全台組込", "#lb-badge-web_01", "up/active", "#lb-badge-web_02", "up/active"),
            ("WEB全台切離-waitなし", "#lb-badge-web_01", "under-maintenance", "#lb-badge-web_02", "under-maintenance"),
            ("WEB全台組込", "#lb-badge-web_01", "up/active", "#lb-badge-web_02", "up/active"),
        ]

        for job_name, target1, expected1, target2, expected2 in jobs_to_test:
            btn = await page.wait_for_selector(f'button:has-text("{job_name}")', timeout=3000)
            await btn.click()
            await page.wait_for_timeout(300)
            val1 = await page.locator(target1).inner_text()
            val2 = await page.locator(target2).inner_text()
            assert expected1 in val1, f"Expected {expected1} in {target1}, got {val1}"
            assert expected2 in val2, f"Expected {expected2} in {target2}, got {val2}"
        print("  All 9 operation jobs verified.")

        # 6-B. Test In-flight Response Guarantee on 3 "waitなし" Cut Jobs:
        # (WEB-A系切離-waitなし, WEB-B系切離-waitなし, WEB全台切離-waitなし)
        # Even on immediate cut, in-flight packets that already passed External LB MUST complete and return green (200 OK)!
        print("\n--- Testing In-flight Response Guarantee on 3 'waitなし' Cut Jobs ---")
        nowait_scenarios = [
            ("WEB-A系切離-waitなし", "A", "WEB-A系組込"),
            ("WEB-B系切離-waitなし", "B", "WEB-B系組込"),
            ("WEB全台切離-waitなし", "ALL", "WEB全台組込")
        ]
        for cut_job, target_sys, rec_job in nowait_scenarios:
            btn_cut = await page.wait_for_selector(f'button:has-text("{cut_job}")', timeout=3000)
            await btn_cut.click()
            has_green = False
            for _ in range(15):
                await page.wait_for_timeout(200)
                sys_filter = '(p.system === "A" || p.system === "B")' if target_sys == 'ALL' else f'p.system === "{target_sys}"'
                ret_cnt = await page.evaluate(f"""() => {{
                    return packets.filter(p => {sys_filter} && p.isReturning && !p.isError && p.errorCode === 200).length;
                }}""")
                if ret_cnt > 0:
                    has_green = True
                    print(f"  Confirmed [{cut_job}]: In-flight AP/DB packets completed and returned green (200 OK)! count={ret_cnt}")
                    break
            assert has_green, f"[{cut_job}] In-flight packets should complete normally and return green!"
            btn_rec = await page.wait_for_selector(f'button:has-text("{rec_job}")', timeout=3000)
            await btn_rec.click()
            await page.wait_for_timeout(600)
        print("  All 3 'waitなし' in-flight response guarantee tests verified.")

        # 6-C. Test Automatic SORRY Promotion on "WEB全台切離" Job (up/standby -> up/active -> up/standby)
        print("\n--- Testing Automatic SORRY Promotion on 'WEB全台切離' Job ---")
        # Ensure initially SORRY is up/standby
        sorry_init = await page.locator("#lb-badge-web_sorry").inner_text()
        print(f"  Initial SORRY status: {sorry_init}")
        assert "up/standby" in sorry_init, "SORRY must initially be up/standby"

        # Execute "WEB全台切離-waitなし"
        btn_all_cut = await page.wait_for_selector('button:has-text("WEB全台切離-waitなし")', timeout=3000)
        await btn_all_cut.click()
        await page.wait_for_timeout(400)

        # Verify SORRY automatically promoted to up/active
        sorry_promoted = await page.locator("#lb-badge-web_sorry").inner_text()
        print(f"  After 'WEB全台切離-waitなし' -> SORRY status: {sorry_promoted}")
        assert "up/active" in sorry_promoted, "SORRY must be automatically promoted to up/active on all WEB cut!"

        # Verify SORRY traffic: clients receive 503 Sorry Page from SORRY server
        found_sorry_traffic = False
        for _ in range(15):
            await page.wait_for_timeout(200)
            sorry_p_count = await page.evaluate("() => packets.filter(p => p.isSorry).length")
            if sorry_p_count > 0:
                found_sorry_traffic = True
                print(f"  Confirmed: SORRY server receiving & responding to traffic! count={sorry_p_count}")
                break
        assert found_sorry_traffic, "SORRY server must handle traffic when promoted!"

        # Recover WEB via "WEB全台組込"
        btn_all_rec = await page.wait_for_selector('button:has-text("WEB全台組込")', timeout=3000)
        await btn_all_rec.click()
        await page.wait_for_timeout(400)

        # Verify SORRY automatically returns to up/standby
        sorry_recovered = await page.locator("#lb-badge-web_sorry").inner_text()
        print(f"  After 'WEB全台組込' -> SORRY status: {sorry_recovered}")
        assert "up/standby" in sorry_recovered, "SORRY must automatically return to up/standby when WEB recovered!"
        print("  Confirmed: Automatic SORRY promotion and demotion on WEB全台切離/組込 verified successfully.")

        # 7. Test SORRY Server Response Flow (Request: Blue -> Response: Green)
        print("\n--- Testing SORRY Traffic Flow (Request -> Response Green Returning) ---")
        btn_sorry_fail = await page.wait_for_selector('button:has-text("WEB全台 down/-")', timeout=3000)
        await btn_sorry_fail.click()
        await page.wait_for_timeout(400)
        sorry_status = await page.locator("#lb-badge-web_sorry").inner_text()
        print(f"  SORRY status: {sorry_status}")
        assert "up/active" in sorry_status

        # Wait for SORRY returning packets (green) to exist
        found_sorry_returning = False
        for _ in range(15):
            await page.wait_for_timeout(300)
            ret_count = await page.evaluate("() => packets.filter(p => p.isSorry && p.isReturning).length")
            if ret_count > 0:
                print(f"  Confirmed: SORRY returning response packets found! (count = {ret_count})")
                found_sorry_returning = True
                break
        assert found_sorry_returning == True, "SORRY server must return green response packets back to clients"

        # Recover WEB
        btn_web_rec = await page.wait_for_selector('button:has-text("WEB全台 復旧")', timeout=3000)
        await btn_web_rec.click()
        await page.wait_for_timeout(400)
        sorry_after = await page.locator("#lb-badge-web_sorry").inner_text()
        assert "up/standby" in sorry_after
        print("  SORRY returned to up/standby successfully.")

        # 8. Test Manual Mode & Single Request on Client Click
        print("\n--- Testing Manual Mode & Single Request Trigger on Client Click ---")
        # In Auto mode, Req badge should NOT be present on client cards
        auto_has_req = await page.evaluate("""() => {
            const html = document.getElementById('clients-list-container').innerHTML;
            return html.includes('⚡Req');
        }""")
        print(f"  Req badge in Auto mode: {auto_has_req}")
        assert not auto_has_req, "Req badge must NOT be shown in Auto mode"

        btn_mode = await page.wait_for_selector("#btn-traffic-mode", timeout=3000)
        await btn_mode.click()
        await page.wait_for_timeout(300)

        # In Manual mode, Req badge SHOULD be present on client cards
        manual_has_req = await page.evaluate("""() => {
            const html = document.getElementById('clients-list-container').innerHTML;
            return html.includes('⚡Req');
        }""")
        print(f"  Req badge in Manual mode: {manual_has_req}")
        assert manual_has_req, "Req badge MUST be shown in Manual mode"

        mode_text = await btn_mode.inner_text()
        print(f"  Traffic Mode after toggle: {mode_text}")
        assert "手動" in mode_text, "Mode button must indicate manual mode"

        is_manual = await page.evaluate("() => State.trafficMode === 'manual'")
        assert is_manual, "State.trafficMode must be 'manual'"

        # Verify that Req Rate slider and Pause button are hidden in manual mode
        visibility_in_manual = await page.evaluate("""() => {
            const btnRun = document.getElementById('btn-run');
            const reqGroup = document.getElementById('req-rate-control-group');
            const manualGuide = document.getElementById('manual-mode-guide-box');
            return {
                btnRunHidden: btnRun ? btnRun.style.display === 'none' : false,
                reqGroupHidden: reqGroup ? reqGroup.style.display === 'none' : false,
                manualGuideVisible: manualGuide ? manualGuide.style.display === 'flex' : false
            };
        }""")
        print(f"  Visibility in manual mode: {visibility_in_manual}")
        assert visibility_in_manual['btnRunHidden'], "Pause button (btn-run) must be HIDDEN in manual mode"
        assert visibility_in_manual['reqGroupHidden'], "Req rate slider must be HIDDEN in manual mode"
        assert visibility_in_manual['manualGuideVisible'], "Manual mode guide must be VISIBLE in manual mode"
        print("  Confirmed: Pause button and Req Rate slider are cleanly hidden in manual mode!")

        # Verify complete initialization on mode switch (requests, sessions, LB states, metrics)
        init_check = await page.evaluate("""() => {
            return {
                packetCount: packets.length,
                allNullSessions: State.clients.slice(0, State.clientCount).every(c => c.sessionId === null),
                allUpActiveWeb: Object.values(State.nodes).filter(n => n.id.startsWith('web_') && n.id !== 'web_sorry').every(n => n.lbStatus === 'up/active'),
                sorryStandby: State.nodes.web_sorry.lbStatus === 'up/standby',
                reqCount: State.stats.totalRequests
            };
        }""")
        print(f"  Initialization check on toggle to manual: {init_check}")
        assert init_check['packetCount'] == 0, "Packets must be immediately cleared on toggle"
        assert init_check['allNullSessions'], "Client sessions must be reset to null on toggle"
        assert init_check['allUpActiveWeb'], "All Web servers must be reset to up/active on toggle"
        assert init_check['sorryStandby'], "SORRY must be reset to up/standby on toggle"
        assert init_check['reqCount'] == 0, "Request stats must be reset to 0 on toggle"
        print("  Confirmed: Requests, sessions, and LB states completely initialized on toggle to manual!")

        # Click Client-01
        print("  Clicking Client-01...")
        await page.click("#card-client-1")
        await page.wait_for_timeout(200)

        # Check that exactly 1 packet from Client-01 was spawned
        spawned = await page.evaluate("""() => {
            return packets.map(p => ({
                clientId: p.clientRef ? p.clientRef.id : null,
                clientName: p.clientRef ? p.clientRef.name : null,
                isReturning: p.isReturning
            }));
        }""")
        print(f"  Packets after Client-01 click: {spawned}")
        assert len(spawned) >= 1, "Clicking client card must spawn a packet"
        assert spawned[0]['clientId'] == 1, f"Spawned packet must originate from Client-01, got {spawned[0]['clientName']}"
        print("  Confirmed: Clicking Client-01 immediately sent 1 request from Client-01!")

        # Click Client-02
        print("  Clicking Client-02...")
        await page.click("#card-client-2")
        await page.wait_for_timeout(200)
        spawned2 = await page.evaluate("""() => {
            return packets.filter(p => p.clientRef && p.clientRef.id === 2).length;
        }""")
        assert spawned2 >= 1, "Clicking Client-02 must spawn packet for Client-02"
        print("  Confirmed: Clicking Client-02 immediately sent 1 request from Client-02!")

        # Toggle back to Auto mode
        await btn_mode.click()
        await page.wait_for_timeout(500)
        is_auto = await page.evaluate("() => State.trafficMode === 'auto'")
        assert is_auto, "State.trafficMode must return to 'auto'"
        auto_count = await page.evaluate("() => packets.length")
        print(f"  Switched back to auto mode. Auto traffic resumed: packets count = {auto_count}")
        assert auto_count > 0, "Traffic auto-generation must resume in auto mode"
        auto_has_req_after = await page.evaluate("() => document.getElementById('clients-list-container').innerHTML.includes('⚡Req')")
        assert not auto_has_req_after, "Req badge must disappear when switched back to Auto mode"

        # Verify that Pause button and Req Rate slider are restored in Auto mode
        visibility_in_auto = await page.evaluate("""() => {
            const btnRun = document.getElementById('btn-run');
            const reqGroup = document.getElementById('req-rate-control-group');
            const manualGuide = document.getElementById('manual-mode-guide-box');
            return {
                btnRunVisible: btnRun ? btnRun.style.display !== 'none' : false,
                reqGroupVisible: reqGroup ? reqGroup.style.display !== 'none' : false,
                manualGuideHidden: manualGuide ? manualGuide.style.display === 'none' : false
            };
        }""")
        print(f"  Visibility restored in auto mode: {visibility_in_auto}")
        assert visibility_in_auto['btnRunVisible'], "Pause button must be restored in Auto mode"
        assert visibility_in_auto['reqGroupVisible'], "Req rate slider must be restored in Auto mode"
        assert visibility_in_auto['manualGuideHidden'], "Manual guide must be hidden in Auto mode"

        # 9. Verify WEB-AP Persistence & 30s Idle Timeout Reset Rules
        print("\n--- Testing WEB-to-AP Persistence, Least-Conn / Lowest Node ID Rule & 30s Idle Timeout ---")
        await page.evaluate("() => initWebApBindings(false)")
        bindings = await page.evaluate("() => State.webApBindings")
        print(f"  Current Web-AP Bindings: {bindings}")
        assert bindings['web_02']['targetApId'] == 'ap_02', "Web-02 must be bound to AP-02"
        assert bindings['web_04']['targetApId'] == 'ap_04', "Web-04 must be bound to AP-04"
        assert bindings['web_01']['targetApId'] == 'ap_01', "Web-01 must be bound to AP-01"
        assert bindings['web_03']['targetApId'] == 'ap_03', "Web-03 must be bound to AP-03"

        # Test timeout simulation: pause auto traffic to test timeout cleanly
        await page.evaluate("""() => {
            State.trafficMode = 'manual';
            State.webApBindings.web_02.idleTimer = 1;
        }""")
        # Wait 1.5s for timer interval to decrement to 0 and reset binding
        await page.wait_for_timeout(1500)
        reset_binding = await page.evaluate("() => State.webApBindings.web_02")
        print(f"  Binding after 1s timeout: {reset_binding}")
        assert reset_binding['targetApId'] is None, "web_02 binding must be reset after timeout"
        assert reset_binding['idleTimer'] == 0, "web_02 idleTimer must be 0 after timeout"

        # Now test re-assignment with least connection rule (equal conn -> lowest node ID AP-02)
        # Ensure AP-02 and AP-04 both have 0 activeConns
        await page.evaluate("""() => {
            State.nodes.ap_02.activeConnections = 0;
            State.nodes.ap_04.activeConnections = 0;
            const client = State.clients.find(c => c.targetWeb === 'Web-02') || State.clients[1];
            client.sessionId = 'TEST-SESS-01';
            client.targetWeb = 'Web-02';
            sendRequestFromClient(client, false);
        }""")
        await page.wait_for_timeout(300)
        new_binding = await page.evaluate("() => State.webApBindings.web_02")
        print(f"  Re-assigned binding for Web-02 (least conn & lowest node rule): {new_binding}")
        assert new_binding['targetApId'] == 'ap_02', "When conn is equal (0 vs 0), Web-02 must be assigned to lowest AP node (AP-02)"
        assert new_binding['idleTimer'] >= 28, "Idle timer must be re-initialized to ~30s"

        # Now test Least Connection rule when AP-02 has higher conn
        await page.evaluate("""() => {
            State.webApBindings.web_02.targetApId = null;
            State.webApBindings.web_02.idleTimer = 0;
            State.nodes.ap_02.activeConnections = 5;
            State.nodes.ap_04.activeConnections = 1;
            const client = State.clients.find(c => c.targetWeb === 'Web-02') || State.clients[1];
            client.sessionId = 'TEST-SESS-01';
            client.targetWeb = 'Web-02';
            sendRequestFromClient(client, false);
        }""")
        await page.wait_for_timeout(300)
        least_conn_binding = await page.evaluate("() => State.webApBindings.web_02")
        print(f"  Re-assigned binding with AP-02 conn=5 vs AP-04 conn=1: {least_conn_binding}")
        assert least_conn_binding['targetApId'] == 'ap_04', "Must be assigned to least-connection AP node (AP-04)"
        print("  Confirmed: Least-connection priority and lowest-node fallback routing verified successfully!")

        # 10. Web-AP Load Balancing Validation Test Case (User Requested Scenario)
        print("\n--- Testing Specific Web-AP Load Balancing Scenario (Manual Mode, A-Webs Down, Least-Conn Routing) ---")
        # 1) 手動モードにする
        print("  Step 1: Switch to Manual mode")
        # Ensure traffic mode is manual (toggle if needed)
        cur_mode = await page.evaluate("() => State.trafficMode")
        if cur_mode != 'manual':
            await page.click("#btn-traffic-mode")
            await page.wait_for_timeout(300)
        mode = await page.evaluate("() => State.trafficMode")
        assert mode == 'manual', f"Expected manual mode, but got {mode}"

        # 2) A系WEBをダウンにする (Web-02, Web-04 down)
        print("  Step 2: Drop A-System Web servers (Web-02, Web-04)")
        await page.evaluate("""() => {
            // Clear any lingering packets from prior tests completely
            packets.length = 0;
            State.nodes.web_02.lbStatus = 'down/-';
            State.nodes.web_04.lbStatus = 'down/-';
            State.nodes.web_01.lbStatus = 'up/active';
            State.nodes.web_03.lbStatus = 'down/-'; // Keep only Web-01 active in B-tier to ensure traffic goes strictly through Web-01
            State.nodes.ap_01.lbStatus = 'up/active';
            State.nodes.ap_03.lbStatus = 'up/active';
            State.nodes.ap_01.activeConnections = 0;
            State.nodes.ap_03.activeConnections = 0;
            State.stayDuration.web = 0.2;
            State.stayDuration.ap = 3.0; // Keep AP processing active long enough to send 2nd request
            // Ensure clients have null sessions and target Web-01
            State.clients.forEach(c => {
                c.sessionId = null;
                c.targetWeb = 'Web-01';
                c.targetAp = null;
                c.system = 'B';
                c.hasSession = false;
            });
            // Reset bindings
            initWebApBindings(true);
        }""")
        status_web = await page.evaluate("""() => ({
            web_02: State.nodes.web_02.lbStatus,
            web_04: State.nodes.web_04.lbStatus,
            web_01: State.nodes.web_01.lbStatus
        })""")
        print(f"  Web statuses: {status_web}")
        assert status_web['web_02'] == 'down/-'
        assert status_web['web_04'] == 'down/-'
        assert status_web['web_01'] == 'up/active'

        # 3) Client 1つ目で手動でリクエストを1つ実行
        print("  Step 3: Trigger manual request from Client-01")
        await page.evaluate("() => sendManualClientRequest(1)")
        await page.wait_for_timeout(200)

        # 4) Client 1つ目がActiveでWEB-01 -> AP-01に通る (AP-01に到達してstayTimer滞留中＝Activeになるのを待機)
        print("  Step 4: Verify Client-01 request reaches AP-01 via Web-01 and is Active")
        client1_active_on_ap01 = False
        for sec in range(35):
            await page.wait_for_timeout(100)
            ap01_state = await page.evaluate("""() => {
                const p = packets.find(p => p.clientRef && p.clientRef.id === 1);
                return {
                    pkt: p ? { web: p.webNode ? p.webNode.name : null, ap: p.apNode ? p.apNode.name : null, stay: p.stayNodeId, ret: p.isReturning } : null,
                    ap01Conn: State.nodes.ap_01.activeConnections,
                    ap03Conn: State.nodes.ap_03.activeConnections
                };
            }""")
            if ap01_state['pkt'] and ap01_state['pkt']['web'] == 'Web-01' and ap01_state['pkt']['ap'] == 'AP-01':
                if ap01_state['ap01Conn'] >= 1:
                    print(f"  Confirmed Client-01 is actively processing on AP-01: {ap01_state}")
                    client1_active_on_ap01 = True
                    break
        assert client1_active_on_ap01, "Client-01 request must reach AP-01 and become Active"

        # 5) WEB01の無通信タイマーがAP01でActiveであることを確認
        print("  Step 5: Verify Web-01 idle timer is Active with AP-01")
        binding_web01 = await page.evaluate("() => State.webApBindings.web_01")
        print(f"  Web-01 binding: {binding_web01}")
        assert binding_web01['targetApName'] == 'AP-01', "Web-01 must be bound to AP-01"
        assert binding_web01['idleTimer'] > 0, "Web-01 idleTimer must be Active (> 0)"

        # 6) Activeの間にClient 2つ目でリクエストを発生
        print("  Step 6: Trigger manual request from Client-02 while Client-01 is Active on AP-01")
        # Client-01 is actively processing on AP-01 (AP-01 activeConnections >= 1, AP-03 activeConnections == 0)
        await page.evaluate("() => sendManualClientRequest(2)")
        await page.wait_for_timeout(200)

        # 7) Client 1つ目がActiveでWEB-01 -> AP-03に通る（APサーバでコネクションが少ないAPサーバに張られる仕様であるため）
        print("  Step 7: Verify Client-02 routes to Web-01 -> AP-03 because AP-03 has fewer connections")
        packet2_routed = False
        for sec in range(25):
            await page.wait_for_timeout(100)
            p2_all = await page.evaluate("""() => {
                return packets.filter(p => p.clientRef && p.clientRef.id === 2).map(p => ({
                    web: p.webNode ? p.webNode.name : null,
                    ap: p.apNode ? p.apNode.name : null,
                    sys: p.system,
                    ret: p.isReturning,
                    err: p.isError,
                    hop: p.currentHop
                }));
            }""")
            if sec == 0 or sec == 3:
                print(f"    [Dbg Client-02 sec={sec}]: {p2_all}")
            p2 = next((p for p in p2_all if p['web'] == 'Web-01' and p['ap'] == 'AP-03'), None)
            if p2:
                print(f"  Confirmed Client-02 in-flight routing: {p2}")
                packet2_routed = True
                break
        assert packet2_routed, "Client-02 must route through Web-01 -> AP-03 because AP-03 has least connections"
        print("  SUCCESS: User-specified Web-AP Least-Connection & Persistence test passed completely!")

        # 11. Verify Client-to-Web Load Balancing (Calm State / Zero-Traffic -> Least Conn & Lowest Node ID)
        print("\n--- Testing Client-to-Web Load Balancing (Zero-Traffic / Least-Conn & Lowest Node ID) ---")
        await page.evaluate("""() => {
            // Restore all web nodes to up/active
            State.nodes.web_01.lbStatus = 'up/active';
            State.nodes.web_02.lbStatus = 'up/active';
            State.nodes.web_03.lbStatus = 'up/active';
            State.nodes.web_04.lbStatus = 'up/active';
            // Set calm state: all activeConnections = 0
            State.nodes.web_01.activeConnections = 0;
            State.nodes.web_02.activeConnections = 0;
            State.nodes.web_03.activeConnections = 0;
            State.nodes.web_04.activeConnections = 0;
            packets.length = 0;
            // Clear client sessions
            State.clients.forEach(c => {
                c.sessionId = null;
                c.targetWeb = null;
                c.targetAp = null;
                c.hasSession = false;
            });
        }""")

        # 11-1. Client-01 (B-sys): When B-webs conn equal (0 vs 0), request must route to lowest node (Web-01)
        await page.evaluate("() => sendManualClientRequest(1)")
        p_calm1 = await page.evaluate("() => packets[packets.length - 1] ? packets[packets.length - 1].webNode.name : null")
        print(f"  Client-01 (B-sys) calm conn=0 -> Assigned to: {p_calm1}")
        assert p_calm1 == 'Web-01', "From calm state (equal conn=0), Client-01 must route to lowest B-node Web-01"

        # 11-2. Client-01 (B-sys): When Web-01 conn=1 vs Web-03 conn=0, request must route to least-conn Web-03
        await page.evaluate("""() => {
            State.nodes.web_01.activeConnections = 1;
            State.nodes.web_03.activeConnections = 0;
            sendManualClientRequest(1);
        }""")
        p_calm2 = await page.evaluate("() => packets[packets.length - 1] ? packets[packets.length - 1].webNode.name : null")
        print(f"  Client-01 (B-sys) Web-01 conn=1 vs Web-03 conn=0 -> Assigned to: {p_calm2}")
        assert p_calm2 == 'Web-03', "When Web-01 is busy, Client-01 must route to least-conn Web-03"

        # 11-3. Client-02 (A-sys): When A-webs conn equal (0 vs 0), request must route to lowest node (Web-02)
        await page.evaluate("""() => {
            State.nodes.web_02.activeConnections = 0;
            State.nodes.web_04.activeConnections = 0;
            sendManualClientRequest(2);
        }""")
        p_calm3 = await page.evaluate("() => packets[packets.length - 1] ? packets[packets.length - 1].webNode.name : null")
        print(f"  Client-02 (A-sys) calm conn=0 -> Assigned to: {p_calm3}")
        assert p_calm3 == 'Web-02', "From calm state (equal conn=0), Client-02 must route to lowest A-node Web-02"

        # 11-4. Client-02 (A-sys): When Web-02 conn=1 vs Web-04 conn=0, request must route to least-conn Web-04
        await page.evaluate("""() => {
            State.nodes.web_02.activeConnections = 1;
            State.nodes.web_04.activeConnections = 0;
            sendManualClientRequest(2);
        }""")
        p_calm4 = await page.evaluate("() => packets[packets.length - 1] ? packets[packets.length - 1].webNode.name : null")
        print(f"  Client-02 (A-sys) Web-02 conn=1 vs Web-04 conn=0 -> Assigned to: {p_calm4}")
        assert p_calm4 == 'Web-04', "When Web-02 is busy, Client-02 must route to least-conn Web-04"
        print("  SUCCESS: Client-to-Web Least-Connection and Lowest-Node-ID routing verified completely!")

        # 12. Verify Auto -> Manual -> Auto System Balance (Must stay strictly 2 for System A, 2 for System B)
        print("\n--- Testing Auto -> Manual -> Auto Mode Toggle Balance (Must be 2 in System A, 2 in System B) ---")
        # Step A: Reset to normal initial state
        await page.evaluate("() => resetToNormal()")
        await page.wait_for_timeout(300)

        # Step B: Toggle to Manual
        print("  Toggling: Auto -> Manual...")
        await page.click("#btn-traffic-mode")
        await page.wait_for_timeout(300)
        mode1 = await page.evaluate("() => State.trafficMode")
        assert mode1 == 'manual', f"Expected manual mode, got {mode1}"

        # Step C: Toggle back to Auto
        print("  Toggling: Manual -> Auto...")
        await page.click("#btn-traffic-mode")
        await page.wait_for_timeout(400)
        mode2 = await page.evaluate("() => State.trafficMode")
        assert mode2 == 'auto', f"Expected auto mode, got {mode2}"

        # Step D: Check 4 clients balance immediately upon returning to auto
        clients_balanced = await page.evaluate("""() => {
            return State.clients.slice(0, 4).map(c => ({
                name: c.name,
                system: c.system,
                targetWeb: c.targetWeb,
                targetAp: c.targetAp,
                sessionId: c.sessionId
            }));
        }""")
        print(f"  Clients upon returning to Auto: {clients_balanced}")
        sys_a_clients = [c for c in clients_balanced if c['system'] == 'A']
        sys_b_clients = [c for c in clients_balanced if c['system'] == 'B']
        print(f"  System A clients ({len(sys_a_clients)}): {[c['name'] for c in sys_a_clients]}")
        print(f"  System B clients ({len(sys_b_clients)}): {[c['name'] for c in sys_b_clients]}")
        assert len(sys_a_clients) == 2, f"System A must have exactly 2 clients, but got {len(sys_a_clients)}: {sys_a_clients}"
        assert len(sys_b_clients) == 2, f"System B must have exactly 2 clients, but got {len(sys_b_clients)}: {sys_b_clients}"

        # Step E: Let auto traffic run for 2.5s and verify it remains 100% balanced (2 for A, 2 for B)
        print("  Running auto traffic for 2.5s to verify sustained system balance...")
        await page.wait_for_timeout(2500)
        sustained_clients = await page.evaluate("""() => {
            return State.clients.slice(0, 4).map(c => ({
                name: c.name,
                system: c.system,
                targetWeb: c.targetWeb,
                targetAp: c.targetAp
            }));
        }""")
        sus_a = [c for c in sustained_clients if c['system'] == 'A']
        sus_b = [c for c in sustained_clients if c['system'] == 'B']
        print(f"  Sustained: System A count={len(sus_a)}, System B count={len(sus_b)}")
        assert len(sus_a) == 2, f"System A must remain exactly 2 clients, but got {len(sus_a)}"
        assert len(sus_b) == 2, f"System B must remain exactly 2 clients, but got {len(sus_b)}"
        print("  SUCCESS: Auto -> Manual -> Auto preserves exact 2-A / 2-B client balance with zero bias!")

        if errors:
            print("\n[ERROR] Errors encountered:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print("\n==========================================")
            print("[SUCCESS] ALL AP STICKY ROUTING & JOB TESTS PASSED!")
            print("==========================================")

        await browser.close()

if __name__ == '__main__':
    asyncio.run(run_tests())
