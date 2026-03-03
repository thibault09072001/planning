import streamlit as st
import pandas as pd
import io
from ortools.sat.python import cp_model
from datetime import datetime

# --- 1. CONFIGURATION DE L'INTERFACE (MODE ULTRA PRO) ---
st.set_page_config(page_title="OptiStaff | Matrice EHPAD", page_icon="🏥", layout="wide", initial_sidebar_state="expanded")

# Injection CSS pour un design SaaS Premium
st.markdown("""
<style>
    /* Bouton d'action principal */
    div.stButton > button {
        background-color: #1A5276 !important;
        color: white !important;
        font-size: 16px !important;
        font-weight: 600 !important;
        padding: 12px 24px !important;
        border-radius: 6px !important;
        border: none !important;
        transition: all 0.3s ease;
    }
    div.stButton > button:hover {
        background-color: #154360 !important;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1) !important;
    }
    /* Bouton de téléchargement vert pro */
    div.stDownloadButton > button {
        background-color: #27AE60 !important;
        color: white !important;
        font-size: 18px !important;
        font-weight: 700 !important;
        padding: 16px 32px !important;
        border-radius: 8px !important;
        border: none !important;
        width: 100% !important;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    div.stDownloadButton > button:hover {
        background-color: #1E8449 !important;
        box-shadow: 0 6px 12px rgba(0,0,0,0.15) !important;
    }
    /* Style des métriques */
    div[data-testid="metric-container"] {
        background-color: #F8F9F9;
        border: 1px solid #E5E8E8;
        padding: 15px;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
    }
</style>
""", unsafe_allow_html=True)

# --- 2. MENU LATÉRAL (SIDEBAR) ---
with st.sidebar:
    st.markdown("### 🏥 OptiStaff EHPAD")
    st.markdown("---")
    
    nb_semaines = st.number_input("Durée du cycle (semaines)", min_value=4, max_value=12, value=4, step=4, help="Sélectionnez un multiple de 4 pour garantir l'équilibre des contrats.")
    
    st.markdown("---")
    st.markdown("**Paramètres du Moteur (v14.0)**")
    st.markdown("""
    <ul style='font-size: 13px; color: #5D6D7E; padding-left: 20px;'>
        <li>Matrice de roulement perpétuelle</li>
        <li>Respect strict des 20j (100%) / 16j (80%)</li>
        <li>Limitation légale : Max 4j consécutifs</li>
        <li>Renforts gérés automatiquement (Matin)</li>
        <li>Répartition équitable des coupés (C)</li>
    </ul>
    """, unsafe_allow_html=True)

# --- 3. ESPACE CENTRAL (TABLEAU DE BORD) ---
st.title("Matrice de Roulement Structurelle")
st.info("💡 **Mode d'emploi :** Saisissez l'effectif cible de votre établissement. Le moteur d'intelligence artificielle se charge d'équilibrer les rythmes de travail, de lisser les horaires coupés et de positionner les vacations nécessaires pour les week-ends.")

st.markdown("### 📊 Indicateurs de Service")
col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
with col_kpi1:
    st.metric(label="Format Matrice", value=f"{nb_semaines} Semaines")
with col_kpi2:
    st.metric(label="Jours à couvrir", value=nb_semaines * 7)
with col_kpi3:
    st.metric(label="Besoins WE (Titulaires)", value="9 / jour")
with col_kpi4:
    st.metric(label="Besoins WE (Vacations)", value="2 / jour")

st.markdown("<br>", unsafe_allow_html=True)

st.markdown("### 👥 Registre du Personnel")
data_base = pd.DataFrame({
    "Nom": [f"Salarié {i+1}" for i in range(15)] + [f"Salarié {i+16}" for i in range(3)],
    "Contrat (%)": [100]*15 + [80]*3
})

# Tableau de saisie plus propre
df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True, hide_index=True)

st.markdown("<br>", unsafe_allow_html=True)

# --- 4. MOTEUR DE RÉSOLUTION ---
if st.button("⚙️ GÉNÉRER LA MATRICE OPTIMISÉE", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    
    noms_titulaires = df_equipe["Nom"].tolist()
    valeurs_contrats = df_equipe["Contrat (%)"].tolist()
    
    # Remplaçants stricts
    noms_complets = noms_titulaires + ["VACATAIRE 1", "VACATAIRE 2"]
    nb_titulaires = len(noms_titulaires)
    total_effectif = len(noms_complets)
    
    with st.spinner("Analyse algorithmique en cours (Création de la boucle temporelle - env. 45 sec)..."):
        jours_cycle = nb_semaines * 7
        postes = ['M', 'A', 'C']
        model = cp_model.CpModel()
        x = {}
        for e in range(total_effectif):
            for d in range(jours_cycle):
                for p in postes:
                    x[(e, d, p)] = model.NewBoolVar(f'staff_{e}_{d}_{p}')
        
        cibles_travail = [] 
        penalites_c = [] 
        mult_cycle = nb_semaines // 4 
        
        # --- LOGIQUE TITULAIRES ---
        for e in range(nb_titulaires):
            cible_jours = int((valeurs_contrats[e] / 100) * 5 * nb_semaines)
            cibles_travail.append(cible_jours)
            
            model.Add(sum(x[(e, d, p)] for d in range(jours_cycle) for p in postes) == cible_jours)
            for d in range(jours_cycle): model.AddAtMostOne(x[(e, d, p)] for p in postes)
            for d in range(jours_cycle): model.AddImplication(x[(e, d, 'A')], x[(e, (d + 1) % jours_cycle, 'M')].Not())
            for d in range(jours_cycle): model.Add(sum(x[(e, (d+i) % jours_cycle, p)] for i in range(5) for p in postes) <= 4)
            
            j_sem = [d for d in range(jours_cycle) if d % 7 < 5]
            j_we = [d for d in range(jours_cycle) if d % 7 >= 5]
            c_sem_var = sum(x[(e, d, 'C')] for d in j_sem)
            c_we_var = sum(x[(e, d, 'C')] for d in j_we)
            
            model.Add(c_sem_var + c_we_var <= 2 * mult_cycle)
            model.Add(c_we_var <= 1 * mult_cycle)
            
            temp_excess = model.NewIntVar(-100, 100, f'temp_excess_{e}')
            model.Add(temp_excess == c_sem_var - (1 * mult_cycle))
            excess_c_sem = model.NewIntVar(0, 100, f'excess_c_sem_{e}')
            model.AddMaxEquality(excess_c_sem, [0, temp_excess])
            penalites_c.append(excess_c_sem)
            
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

        # --- LOGIQUE VACATAIRES (Uniquement WE) ---
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
            m_t, a_t, c_t = (6, 3, 2) if is_we else (8, 4, 1)
            
            model.Add(sum(x[(e, d, 'M')] for e in range(total_effectif)) >= m_t) 
            model.Add(sum(x[(e, d, 'A')] for e in range(total_effectif)) == a_t)
            model.Add(sum(x[(e, d, 'C')] for e in range(total_effectif)) == c_t)
            if is_we:
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires) for p in postes) == 9)
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires, total_effectif) for p in postes) == 2)

        # --- OPTIMISATION ---
        model.Minimize(sum(penalites_c))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 75.0 
        statut = solver.Solve(model)

        if statut in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            resultats, noms_utilises, audit_data = [], [], []
            for e in range(total_effectif):
                ligne, j_t, c_s, c_w = [], 0, 0, 0
                for d in range(jours_cycle):
                    v = "" # Vide au lieu de "Repos" pour alléger visuellement
                    for p in postes:
                        if solver.Value(x[(e, d, p)]) == 1: 
                            v, j_t = p, j_t + 1
                            if p == 'C':
                                if d % 7 < 5: c_s += 1
                                else: c_w += 1
                    ligne.append(v)
                
                enchain_am = sum(1 for d in range(jours_cycle) if ligne[d] == 'A' and ligne[(d+1)%jours_cycle] == 'M')
                resultats.append(ligne)
                noms_utilises.append(noms_complets[e])
                
                if e < nb_titulaires:
                    etoile = "⚠️ " if c_s > 1 * mult_cycle else "✓ "
                    audit_data.append(f"{etoile}{j_t}j | {c_s}C Sem / {c_w}C WE | {enchain_am} AM")
                else:
                    audit_data.append(f"VACATION | {j_t}j WE")

            # --- EXPORT EXCEL ULTRA PRO ---
            df_final = pd.DataFrame(resultats, index=noms_utilises)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                # Démarrage ligne 6 pour laisser la place au Header pro
                df_final.to_excel(writer, sheet_name='Matrice RH', startrow=6, header=False)
                wb, ws = writer.book, writer.sheets['Matrice RH']
                
                # --- PALETTE CORPORATE ---
                f_titre = wb.add_format({'bold': True, 'font_size': 18, 'align': 'left', 'valign': 'vcenter', 'font_color': '#1A252F'})
                f_date = wb.add_format({'font_size': 10, 'align': 'left', 'font_color': '#7F8C8D', 'italic': True})
                
                # Légende
                f_leg_m = wb.add_format({'bg_color': '#D4E6F1', 'font_color': '#154360', 'align': 'center', 'border': 1, 'bold': True})
                f_leg_a = wb.add_format({'bg_color': '#FCF3CF', 'font_color': '#7D6608', 'align': 'center', 'border': 1, 'bold': True})
                f_leg_c = wb.add_format({'bg_color': '#FADBD8', 'font_color': '#78281F', 'align': 'center', 'border': 1, 'bold': True})
                f_leg_txt = wb.add_format({'align': 'left', 'valign': 'vcenter'})
                
                # Tableau
                f_sem = wb.add_format({'bold': True, 'bg_color': '#34495E', 'font_color': 'white', 'border': 1, 'align': 'center'})
                f_jour = wb.add_format({'bold': True, 'bg_color': '#ECF0F1', 'font_color': '#2C3E50', 'border': 1, 'align': 'center'})
                
                f_m = wb.add_format({'bg_color': '#D4E6F1', 'font_color': '#154360', 'align': 'center', 'border': 1, 'bold': True})
                f_a = wb.add_format({'bg_color': '#FCF3CF', 'font_color': '#7D6608', 'align': 'center', 'border': 1, 'bold': True})
                f_c = wb.add_format({'bg_color': '#FADBD8', 'font_color': '#78281F', 'align': 'center', 'border': 1, 'bold': True})
                f_remp = wb.add_format({'bg_color': '#E67E22', 'font_color': 'white', 'bold': True, 'align': 'center', 'border': 1})
                f_repos = wb.add_format({'bg_color': '#FFFFFF', 'border': 1})
                f_audit = wb.add_format({'font_color': '#34495E', 'font_size': 10, 'border': 1, 'bg_color': '#FDFEFE', 'align': 'center'})
                
                # Configuration grille
                ws.set_column('A:A', 28)
                ws.set_column(1, jours_cycle, 5)
                ws.set_column(jours_cycle+1, jours_cycle+1, 35)
                ws.set_default_row(20)
                ws.freeze_panes(6, 1)
                
                # --- EN-TÊTE ---
                date_gen = datetime.now().strftime('%d/%m/%Y à %H:%M')
                ws.write(0, 0, "MATRICE DE ROULEMENT EHPAD", f_titre)
                ws.write(1, 0, f"Document généré le {date_gen} | Cycle : {nb_semaines} semaines", f_date)
                
                # Légende intégrée
                ws.write(3, 0, "Légende des postes :", wb.add_format({'bold': True}))
                ws.write(3, 1, "M", f_leg_m)
                ws.write(3, 2, "Matin", f_leg_txt)
                ws.write(3, 3, "A", f_leg_a)
                ws.write(3, 4, "Après-midi", f_leg_txt)
                ws.write(3, 5, "C", f_leg_c)
                ws.write(3, 6, "Coupé", f_leg_txt)
                
                # --- STRUCTURE TABLEAU ---
                # Ligne des Semaines
                for w in range(nb_semaines):
                    ws.merge_range(4, (w*7)+1, 4, (w*7)+7, f"SEMAINE {w+1}", f_sem)
                
                # Ligne des Jours
                ws.write(5, 0, "Employés", f_jour)
                jours_lettres = ["L", "M", "M", "J", "V", "S", "D"]
                for c in range(jours_cycle): 
                    ws.write(5, c+1, jours_lettres[c%7], f_jour)
                ws.write(5, jours_cycle+1, "AUDIT STRUCTUREL", f_jour)
                
                # --- DONNÉES ---
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
            
            st.success("✅ Matrice RH générée avec succès !")
            st.download_button("📥 TÉLÉCHARGER LE FICHIER EXCEL", buffer.getvalue(), "Matrice_OptiStaff_EHPAD.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.error("❌ Les contraintes contractuelles sont incompatibles avec l'effectif actuel.")
