import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model

# --- 1. CONFIGURATION DE L'INTERFACE (PURE RH) ---
st.set_page_config(page_title="Générateur de Trame | EHPAD", page_icon="🏥", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
div.stDownloadButton > button {
    background-color: #28a745 !important;
    color: white !important;
    font-size: 18px !important;
    font-weight: bold !important;
    padding: 15px 30px !important;
    border-radius: 8px !important;
    border: none !important;
    width: 100% !important;
}
</style>
""", unsafe_allow_html=True)

# --- 2. MENU LATÉRAL ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2966/2966327.png", width=60) 
    st.title("Configuration")
    st.markdown("---")
    
    nb_semaines = st.number_input("⏱️ Durée du cycle (semaines)", min_value=4, max_value=12, value=4, step=4)
    
    st.markdown("---")
    st.caption("🔒 Moteur de Matrice v12.0")
    st.caption("✓ Roulement Infini\n✓ Contrats Stricts\n✓ Max 4j consécutifs\n✓ 9 Titulaires / 2 Remplaçants WE\n✓ Équité Coupés (16x 1/1 + 2x 2/0)")

# --- 3. ESPACE CENTRAL ---
st.title("Génération de la Matrice de Roulement")
st.info("Cette interface génère la trame de base de votre établissement. Les absences seront gérées ultérieurement par le remplacement des postes vacants.")

col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
with col_kpi1:
    st.metric(label="Taille du cycle", value=f"{nb_semaines} semaines")
with col_kpi2:
    st.metric(label="Postes Titulaires / WE", value="9")
with col_kpi3:
    st.metric(label="Postes Remplaçants / WE", value="2")

st.markdown("<br>", unsafe_allow_html=True)

# Registre simplifié sans colonnes d'absences
data_base = pd.DataFrame({
    "Nom": [f"Salarié {i+1}" for i in range(15)] + [f"Salarié {i+16}" for i in range(3)],
    "Contrat (%)": [100]*15 + [80]*3
})

st.subheader("Configuration de l'équipe titulaire")
df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

# --- 4. MOTEUR DE RÉSOLUTION ---
if st.button("🚀 GÉNÉRER LA MATRICE DE RÉFÉRENCE", type="primary", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    
    noms_titulaires = df_equipe["Nom"].tolist()
    valeurs_contrats = df_equipe["Contrat (%)"].tolist()
    
    # 2 Remplaçants fixes pour le week-end
    noms_complets = noms_titulaires + ["REMPLAÇANT 1", "REMPLAÇANT 2"]
    nb_titulaires = len(noms_titulaires)
    total_effectif = len(noms_complets)
    
    with st.spinner("Calcul de la matrice optimale..."):
        jours_cycle = nb_semaines * 7
        postes = ['M', 'A', 'C']
        model = cp_model.CpModel()
        x = {}
        for e in range(total_effectif):
            for d in range(jours_cycle):
                for p in postes:
                    x[(e, d, p)] = model.NewBoolVar(f's_{e}_{d}_{p}')
        
        groupe_special_coupures = []
        mult_cycle = nb_semaines // 4 
        
        # --- LOGIQUE TITULAIRES ---
        for e in range(nb_titulaires):
            # Cible contrat (ex: 20j pour 100%)
            cible_jours = int((valeurs_contrats[e] / 100) * 5 * nb_semaines)
            model.Add(sum(x[(e, d, p)] for d in range(jours_cycle) for p in postes) == cible_jours)
            
            for d in range(jours_cycle): model.AddAtMostOne(x[(e, d, p)] for p in postes)
            
            # Repos Circulaire (A -> M interdit entre fin et début de cycle)
            for d in range(jours_cycle): 
                model.AddImplication(x[(e, d, 'A')], x[(e, (d + 1) % jours_cycle, 'M')].Not())
            
            # Max 4 Jours Circulaire
            for d in range(jours_cycle): 
                model.Add(sum(x[(e, (d+i) % jours_cycle, p)] for i in range(5) for p in postes) <= 4)
            
            # Équité Coupés 'C'
            j_sem = [d for d in range(jours_cycle) if d % 7 < 5]
            j_we = [d for d in range(jours_cycle) if d % 7 >= 5]
            est_special = model.NewBoolVar(f'spec_{e}')
            groupe_special_coupures.append(est_special)
            model.Add(sum(x[(e, d, 'C')] for d in j_sem) == (1 * mult_cycle) + est_special)
            model.Add(sum(x[(e, d, 'C')] for d in j_we) == (1 * mult_cycle) - est_special)
            
            # Week-ends
            ind_we = []
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                actif = model.NewBoolVar(f'we_a_{e}_{w}')
                model.Add(sum(x[(e, sat, p)] for p in postes) == sum(x[(e, sun, p)] for p in postes))
                model.AddMaxEquality(actif, [x[(e, sat, p)] for p in postes])
                ind_we.append(actif)
                model.Add(sum(x[(e, d, p)] for d in range(w*7, w*7+7) for p in postes) <= 4 + (2 * actif))

            # Week-ends alternés (Circulaire)
            for w in range(nb_semaines): 
                model.Add(ind_we[w] + ind_we[(w + 1) % nb_semaines] <= 1)

        # Force les 2 spéciaux (2 sem / 0 WE)
        model.Add(sum(groupe_special_coupures) == 2 * mult_cycle)

        # --- LOGIQUE REMPLAÇANTS (WE uniquement) ---
        for e in range(nb_titulaires, total_effectif):
            for d in range(jours_cycle):
                model.AddAtMostOne(x[(e, d, p)] for p in postes)
                if d % 7 < 5: # Interdit en semaine
                    for p in postes: model.Add(x[(e, d, p)] == 0)
                if d % 7 == 5: # Bloc WE
                    model.Add(sum(x[(e, d, p)] for p in postes) == sum(x[(e, d+1, p)] for p in postes))
                model.Add(x[(e, d, 'C')] == 0) # Pas de coupé pour les remplaçants

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

        # Optimisation
        model.Maximize(sum(x[(e, d, p)] for e in range(nb_titulaires) for d in range(jours_cycle) for p in postes))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 60.0 
        statut = solver.Solve(model)

        if statut in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            resultats, noms_utilises, audit_data = [], [], []
            for e in range(total_effectif):
                ligne, j_t, c_s, c_w = [], 0, 0, 0
                for d in range(jours_cycle):
                    v = "Repos"
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
                
                etoile = "🌟 " if c_s > c_w else "✅ "
                audit_data.append(f"{etoile}{j_t}j | {c_s}C Sem / {c_w}C WE | {enchain_am} A->M")

            # --- EXCEL ---
            df_final = pd.DataFrame(resultats, index=noms_utilises)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name='Matrice', startrow=3, header=False)
                wb, ws = writer.book, writer.sheets['Matrice']
                
                # Styles
                f_titre = wb.add_format({'bold': True, 'font_size': 16, 'align': 'center', 'bg_color': '#2C3E50', 'font_color': 'white'})
                f_sem = wb.add_format({'bold': True, 'bg_color': '#D5DBDB', 'border': 1, 'align': 'center'})
                f_jour = wb.add_format({'bold': True, 'bg_color': '#EAEDED', 'border': 1, 'align': 'center'})
                f_m = wb.add_format({'bg_color': '#E0F2F1', 'font_color': '#00695C', 'align': 'center', 'border': 1})
                f_a = wb.add_format({'bg_color': '#FFF3E0', 'font_color': '#E65100', 'align': 'center', 'border': 1})
                f_c = wb.add_format({'bg_color': '#FFEBEE', 'font_color': '#B71C1C', 'align': 'center', 'border': 1})
                f_remp = wb.add_format({'bg_color': '#34495E', 'font_color': 'white', 'bold': True, 'align': 'center', 'border': 1})
                f_audit = wb.add_format({'font_color': '#34495E', 'bold': True, 'border': 1, 'bg_color': '#F4F6F6'})
                
                ws.set_column('A:A', 25)
                ws.set_column(1, jours_cycle, 6)
                ws.set_column(jours_cycle+1, jours_cycle+1, 40)
                ws.freeze_panes(3, 1)
                
                ws.merge_range(0, 0, 0, jours_cycle+1, "MATRICE DE ROULEMENT PERPÉTUELLE EHPAD", f_titre)
                for w in range(nb_semaines):
                    ws.merge_range(1, (w*7)+1, 1, (w*7)+7, f"SEMAINE {w+1}", f_sem)
                ws.write(2, 0, "Employés", f_jour)
                jours_lettres = ["L", "M", "M", "J", "V", "S", "D"]
                for c in range(jours_cycle): ws.write(2, c+1, jours_lettres[c%7], f_jour)
                ws.write(2, jours_cycle+1, "AUDIT QUALITÉ TRAME", f_jour)
                
                for r in range(len(noms_utilises)):
                    is_remp = "REMPLAÇANT" in noms_utilises[r]
                    for c in range(jours_cycle):
                        val = resultats[r][c]
                        fmt = f_remp if is_remp and val != "Repos" else f_m if val == 'M' else f_a if val == 'A' else f_c if val == 'C' else None
                        ws.write(r+3, c+1, val, fmt)
                    ws.write(r+3, jours_cycle+1, audit_data[r], f_audit)
            
            st.success("✅ Matrice de roulement générée.")
            st.download_button("📥 TÉLÉCHARGER LA MATRICE", buffer.getvalue(), "Matrice_Roulement.xlsx")
        else:
            st.error("❌ Les contraintes sont trop strictes pour l'effectif actuel.")
