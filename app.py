import streamlit as st
import pandas as pd
import io
from ortools.sat.python import cp_model
from datetime import datetime

# --- 1. CONFIGURATION DE L'INTERFACE ---
st.set_page_config(page_title="OptiStaff | Matrice EHPAD", page_icon="🏥", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    div.stButton > button {
        background-color: #1A5276 !important;
        color: white !important;
        font-size: 16px !important;
        font-weight: 600 !important;
        padding: 12px 24px !important;
        border-radius: 6px !important;
        border: none !important;
    }
    div.stDownloadButton > button {
        background-color: #27AE60 !important;
        color: white !important;
        font-size: 18px !important;
        font-weight: 700 !important;
        padding: 16px 32px !important;
        border-radius: 8px !important;
        border: none !important;
        width: 100% !important;
    }
    div[data-testid="metric-container"] {
        background-color: #F8F9F9;
        border: 1px solid #E5E8E8;
        padding: 15px;
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)

# --- 2. MENU LATÉRAL ---
with st.sidebar:
    st.markdown("### 🏥 OptiStaff EHPAD")
    st.markdown("---")
    nb_semaines = st.number_input("Durée du cycle (semaines)", min_value=4, max_value=12, value=4, step=4)
    st.markdown("---")
    st.markdown("**Paramètres du Moteur (v14.2)**")
    st.markdown("""
    <ul style='font-size: 13px; color: #5D6D7E; padding-left: 20px;'>
        <li>Équilibre automatique Matin / Après-midi</li>
        <li>Max 1 Coupé par semaine (Remplacé par M/A si besoin)</li>
        <li>Matrice de roulement perpétuelle</li>
        <li>Respect strict des contrats</li>
        <li>Max 4j consécutifs</li>
    </ul>
    """, unsafe_allow_html=True)

# --- 3. ESPACE CENTRAL ---
st.title("Matrice de Roulement Structurelle")
st.info("💡 **Nouveauté :** Le moteur équilibre désormais automatiquement le nombre de Matins et d'Après-midis pour chaque soignant, tout en limitant les coupés à 1 par semaine maximum.")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Format Matrice", f"{nb_semaines} Semaines")
col2.metric("Jours à couvrir", nb_semaines * 7)
col3.metric("Besoins WE", "9 Titulaires")
col4.metric("Vacations WE", "2 Remplaçants")

st.markdown("<br>", unsafe_allow_html=True)
st.markdown("### 👥 Registre du Personnel")

data_base = pd.DataFrame({
    "Nom": [f"Salarié {i+1}" for i in range(15)] + [f"Salarié {i+16}" for i in range(3)],
    "Contrat (%)": [100]*15 + [80]*3
})
df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True, hide_index=True)

st.markdown("<br>", unsafe_allow_html=True)

# --- 4. MOTEUR DE RÉSOLUTION ---
if st.button("⚙️ GÉNÉRER LA MATRICE OPTIMISÉE", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    
    noms_titulaires = df_equipe["Nom"].tolist()
    valeurs_contrats = df_equipe["Contrat (%)"].tolist()
    
    noms_complets = noms_titulaires + ["VACATAIRE 1", "VACATAIRE 2"]
    nb_titulaires = len(noms_titulaires)
    total_effectif = len(noms_complets)
    
    with st.spinner("Équilibrage des Matins/Après-midis et lissage des Coupés (env. 45 sec)..."):
        jours_cycle = nb_semaines * 7
        postes = ['M', 'A', 'C']
        model = cp_model.CpModel()
        x = {}
        for e in range(total_effectif):
            for d in range(jours_cycle):
                for p in postes:
                    x[(e, d, p)] = model.NewBoolVar(f'staff_{e}_{d}_{p}')
        
        cibles_travail = [] 
        penalites_ma = [] # Pour équilibrer Matin et Après-midi
        manque_c = []     # Pour distribuer les coupés au maximum
        mult_cycle = nb_semaines // 4 
        
        # --- LOGIQUE TITULAIRES ---
        for e in range(nb_titulaires):
            cible_jours = int((valeurs_contrats[e] / 100) * 5 * nb_semaines)
            cibles_travail.append(cible_jours)
            
            model.Add(sum(x[(e, d, p)] for d in range(jours_cycle) for p in postes) == cible_jours)
            for d in range(jours_cycle): model.AddAtMostOne(x[(e, d, p)] for p in postes)
            for d in range(jours_cycle): model.AddImplication(x[(e, d, 'A')], x[(e, (d + 1) % jours_cycle, 'M')].Not())
            for d in range(jours_cycle): model.Add(sum(x[(e, (d+i) % jours_cycle, p)] for i in range(5) for p in postes) <= 4)
            
            # ÉQUILIBRE MATIN / APRÈS-MIDI
            m_tot = sum(x[(e, d, 'M')] for d in range(jours_cycle))
            a_tot = sum(x[(e, d, 'A')] for d in range(jours_cycle))
            ecart_ma = model.NewIntVar(-30, 30, f'ecart_ma_{e}')
            model.Add(ecart_ma == m_tot - a_tot)
            abs_ecart_ma = model.NewIntVar(0, 30, f'abs_ecart_ma_{e}')
            model.AddAbsEquality(abs_ecart_ma, ecart_ma)
            penalites_ma.append(abs_ecart_ma)
            
            # GESTION DES COUPÉS (Max 1 par semaine, compensé en M/A)
            j_sem = [d for d in range(jours_cycle) if d % 7 < 5]
            j_we = [d for d in range(jours_cycle) if d % 7 >= 5]
            c_sem_var = sum(x[(e, d, 'C')] for d in j_sem)
            c_we_var = sum(x[(e, d, 'C')] for d in j_we)
            
            model.Add(c_sem_var <= 1 * mult_cycle)
            model.Add(c_we_var <= 1 * mult_cycle)
            
            # On pousse l'IA à donner 1 coupé en semaine (s'il en manque, ça pénalise légèrement)
            manque_c_sem = model.NewIntVar(0, 10, f'manque_c_{e}')
            model.Add(manque_c_sem == (1 * mult_cycle) - c_sem_var)
            manque_c.append(manque_c_sem)
            
            ind_we = []
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                actif = model.NewBoolVar(f'we_a_{e}_{w}')
                model.Add(sum(x[(e, sat, p)] for p in postes) == sum(x[(e, sun, p)] for p in postes))
                model.AddMaxEquality(actif, [x[(e, sat, p)] for p in postes])
                ind_we.append(actif)
                model.Add(sum(x[(e, d, p)] for d in range(w*7, w*7+7) for p in postes) <= 4 + (2 * actif))

            for w in range(nb_semaines): 
                model.Add(ind_we[w] + ind_we[(w + 1) % nb_semaines] <= 1)

        # --- LOGIQUE VACATAIRES ---
        for e in range(nb_titulaires, total_effectif):
            cibles_travail.append("Vac.") 
            for d in range(jours_cycle):
                model.AddAtMostOne(x[(e, d, p)] for p in postes)
                if d % 7 < 5: 
                    for p in postes: model.Add(x[(e, d, p)] == 0) 
                if d % 7 == 5: 
                    model.Add(sum(x[(e, d, p)] for p in postes) == sum(x[(e, d+1, p)] for p in postes))
                model.Add(x[(e, d, 'C')] == 0)

        # --- QUOTAS EHPAD ---
        for d in range(jours_cycle):
            is_we = (d % 7 >= 5)
            if is_we:
                model.Add(sum(x[(e, d, 'M')] for e in range(total_effectif)) >= 6)
                model.Add(sum(x[(e, d, 'A')] for e in range(total_effectif)) == 3)
                model.Add(sum(x[(e, d, 'C')] for e in range(total_effectif)) == 2)
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires) for p in postes) == 9)
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires, total_effectif) for p in postes) == 2)
            else:
                # La souplesse est ici : Les 'C' manquants deviennent des 'M' ou des 'A'
                model.Add(sum(x[(e, d, 'M')] for e in range(total_effectif)) >= 8)
                model.Add(sum(x[(e, d, 'A')] for e in range(total_effectif)) >= 4)
                model.Add(sum(x[(e, d, 'C')] for e in range(total_effectif)) <= 1)

        # --- OPTIMISATION GLOBALE ---
        # 1. On équilibre les M et A (Priorité haute)
        # 2. On essaie de placer un maximum de coupés (dans la limite de 1/personne) pour soulager la semaine
        model.Minimize(sum(penalites_ma) * 10 + sum(manque_c) * 5)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 75.0 
        statut = solver.Solve(model)

        if statut in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            resultats, noms_utilises, audit_data = [], [], []
            for e in range(total_effectif):
                ligne, j_t, c_s, c_w, m_t, a_t = [], 0, 0, 0, 0, 0
                for d in range(jours_cycle):
                    v = "" 
                    for p in postes:
                        if solver.Value(x[(e, d, p)]) == 1: 
                            v, j_t = p, j_t + 1
                            if p == 'C':
                                if d % 7 < 5: c_s += 1
                                else: c_w += 1
                            elif p == 'M': m_t += 1
                            elif p == 'A': a_t += 1
                    ligne.append(v)
                
                enchain_am = sum(1 for d in range(jours_cycle) if ligne[d] == 'A' and ligne[(d+1)%jours_cycle] == 'M')
                resultats.append(ligne)
                noms_utilises.append(noms_complets[e])
                
                if e < nb_titulaires:
                    etoile = "🌟 " if c_w == 0 else "✓ "
                    audit_data.append(f"{etoile}{j_t}j | {c_s}C Sem / {c_w}C WE | {m_t}M / {a_t}A | {enchain_am} AM")
                else:
                    audit_data.append(f"VACATION | {m_t}M / {a_t}A")

            # --- EXPORT EXCEL ---
            df_final = pd.DataFrame(resultats, index=noms_utilises)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name='Matrice RH', startrow=6, header=False)
                wb, ws = writer.book, writer.sheets['Matrice RH']
                
                f_titre = wb.add_format({'bold': True, 'font_size': 18, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#2C3E50', 'font_color': 'white'})
                f_date = wb.add_format({'font_size': 10, 'align': 'center', 'font_color': '#7F8C8D', 'italic': True})
                f_leg_m = wb.add_format({'bg_color': '#D4E6F1', 'font_color': '#154360', 'align': 'center', 'border': 1, 'bold': True})
                f_leg_a = wb.add_format({'bg_color': '#FCF3CF', 'font_color': '#7D6608', 'align': 'center', 'border': 1, 'bold': True})
                f_leg_c = wb.add_format({'bg_color': '#FADBD8', 'font_color': '#78281F', 'align': 'center', 'border': 1, 'bold': True})
                f_leg_txt = wb.add_format({'align': 'left', 'valign': 'vcenter'})
                f_sem = wb.add_format({'bold': True, 'bg_color': '#34495E', 'font_color': 'white', 'border': 1, 'align': 'center'})
                f_jour = wb.add_format({'bold': True, 'bg_color': '#ECF0F1', 'font_color': '#2C3E50', 'border': 1, 'align': 'center'})
                f_m = wb.add_format({'bg_color': '#D4E6F1', 'font_color': '#154360', 'align': 'center', 'border': 1, 'bold': True})
                f_a = wb.add_format({'bg_color': '#FCF3CF', 'font_color': '#7D6608', 'align': 'center', 'border': 1, 'bold': True})
                f_c = wb.add_format({'bg_color': '#FADBD8', 'font_color': '#78281F', 'align': 'center', 'border': 1, 'bold': True})
                f_remp = wb.add_format({'bg_color': '#E67E22', 'font_color': 'white', 'bold': True, 'align': 'center', 'border': 1})
                f_repos = wb.add_format({'bg_color': '#FFFFFF', 'border': 1})
                f_audit = wb.add_format({'font_color': '#34495E', 'font_size': 10, 'border': 1, 'bg_color': '#FDFEFE', 'align': 'left'})
                
                ws.set_column('A:A', 28)
                ws.set_column(1, jours_cycle, 5)
                ws.set_column(jours_cycle+1, jours_cycle+1, 48) # Élargi pour l'audit M/A
                ws.set_default_row(20)
                ws.freeze_panes(6, 1)
                
                date_gen = datetime.now().strftime('%d/%m/%Y à %H:%M')
                ws.merge_range(0, 0, 0, jours_cycle + 1, "MATRICE DE ROULEMENT EHPAD", f_titre)
                ws.merge_range(1, 0, 1, jours_cycle + 1, f"Document généré le {date_gen} | Cycle : {nb_semaines} semaines", f_date)
                
                ws.write(3, 0, "Légende des postes :", wb.add_format({'bold': True}))
                ws.write(3, 1, "M", f_leg_m)
                ws.write(3, 2, "Matin", f_leg_txt)
                ws.write(3, 3, "A", f_leg_a)
                ws.write(3, 4, "Après-midi", f_leg_txt)
                ws.write(3, 5, "C", f_leg_c)
                ws.write(3, 6, "Coupé", f_leg_txt)
                
                for w in range(nb_semaines):
                    ws.merge_range(4, (w*7)+1, 4, (w*7)+7, f"SEMAINE {w+1}", f_sem)
                
                ws.write(5, 0, "Employés", f_jour)
                jours_lettres = ["L", "M", "M", "J", "V", "S", "D"]
                for c in range(jours_cycle): 
                    ws.write(5, c+1, jours_lettres[c%7], f_jour)
                ws.write(5, jours_cycle+1, "AUDIT STRUCTUREL", f_jour)
                
                for r in range(len(noms_utilises)):
                    is_remp = "VACATAIRE" in noms_utilises[r]
                    for c in range(jours_cycle):
                        val = resultats[r][c]
                        if is_remp and val != "": fmt = f_remp
                        elif val == 'M': fmt = f_m
                        elif val == 'A': fmt = f_a
                        elif val == 'C': fmt = f_c
                        else: fmt = f_repos
                        ws.write(r+6, c+1, val, fmt)
                    ws.write(r+6, jours_cycle+1, audit_data[r], f_audit)
            
            st.success("✅ Matrice RH générée ! M/A équilibrés avec succès.")
            st.download_button("📥 TÉLÉCHARGER LE FICHIER EXCEL", buffer.getvalue(), "Matrice_OptiStaff_Equilibree.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.error("❌ Les contraintes contractuelles sont incompatibles avec l'effectif actuel.")
